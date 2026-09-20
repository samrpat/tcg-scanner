"""Passwords and logged-in devices.

This application spent its whole life with no authentication. Every endpoint was reachable by
anything that could open a socket to it, including the ones that delete inventory and the one
that publishes photographs to the internet. On a home network that is every device on the
network. That is the gap this closes.

**scrypt, from the standard library.** Not bcrypt and not argon2, both of which mean a compiled
wheel — and on an arm64 Pi image a compiled wheel is a thing that can fail to exist. scrypt is a
memory-hard KDF designed for exactly this, `hashlib` has shipped it since 3.6, and the cost
parameters live in the stored string so they can be raised later without invalidating the
password anyone already has.

**Sessions are rows, not signed cookies.** A self-contained token is less machinery, but the
only way to revoke one is to rotate the signing secret, which logs every device out. That is
drastic enough that nobody does it, which means in practice a lost phone stays logged in for
ever. A row per device can be deleted on its own.

**The token is never stored.** Only its sha256. A database backup that leaks is then a set of
useless hashes rather than a set of working cookies — and this project writes backups to disk
by default.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models import AuthSession, User

log = get_logger(__name__)

# ~16 MB and roughly a tenth of a second on a Pi 5. High enough to make an offline attack on a
# leaked hash expensive, low enough that logging in on a phone does not feel broken.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

# Short passwords are the failure mode here, not complexity rules. A single-user app on a home
# network does not need a character-class policy; it needs a password that is not "pokemon".
MIN_PASSWORD_LENGTH = 10

TOKEN_BYTES = 32


class AuthError(Exception):
    """Something the caller can fix, surfaced as a 4xx."""


# ── passwords ──────────────────────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$key`, all base64. Parameters travel with the hash."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"a password needs at least {MIN_PASSWORD_LENGTH} characters; "
            "a phrase you can type on a phone beats a short one full of symbols"
        )
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
        maxmem=SCRYPT_N * SCRYPT_R * 256,
    )
    salt_b64 = base64.b64encode(salt).decode()
    key_b64 = base64.b64encode(key).decode()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${key_b64}"


def verify_password(password: str, encoded: str | None) -> bool:
    """Constant-time check against a stored hash.

    Returns False rather than raising for anything malformed: a corrupt hash column must read
    as "wrong password", not as a 500 that tells an attacker they found something interesting.
    """
    if not encoded:
        return False
    try:
        scheme, n, r, p, salt_b64, key_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=int(n) * int(r) * 256,
        )
    except Exception:  # noqa: BLE001 - any malformed stored hash is simply a failed login
        return False
    return hmac.compare_digest(candidate, expected)


# ── sessions ───────────────────────────────────────────────────────────────────────────────


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_session(
    session: AsyncSession, user: User, *, days: int, label: str | None = None
) -> str:
    """Create a logged-in device and return its token. The token is returned once, never read
    back — after this call only its hash exists."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    session.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_hash(token),
            label=(label or "")[:200] or None,
            expires_at=datetime.now(UTC) + timedelta(days=days),
        )
    )
    await session.flush()
    log.info("auth.session_issued", user=str(user.id), days=days)
    return token


async def user_for_token(session: AsyncSession, token: str) -> User | None:
    """The user this token belongs to, or None.

    Also stamps `last_seen_at`, which is what makes the device list readable — "iPhone, last
    seen 3 minutes ago" is the line that tells you whether a session is yours.
    """
    if not token:
        return None
    row = (
        await session.execute(
            select(AuthSession).where(AuthSession.token_hash == token_hash(token))
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        await session.delete(row)
        return None

    row.last_seen_at = datetime.now(UTC)
    return await session.get(User, row.user_id)


async def revoke_token(session: AsyncSession, token: str) -> bool:
    row = (
        await session.execute(
            select(AuthSession).where(AuthSession.token_hash == token_hash(token))
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    return True


async def revoke_all(session: AsyncSession, user: User, *, keep: str | None = None) -> int:
    """Log every device out. `keep` spares the one making the request, which is what a password
    change wants: everything else is signed out, and you are not."""
    query = delete(AuthSession).where(AuthSession.user_id == user.id)
    if keep:
        query = query.where(AuthSession.token_hash != token_hash(keep))
    result = await session.execute(query)
    return int(result.rowcount or 0)


async def revoke_one(session: AsyncSession, user: User, session_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            select(AuthSession).where(
                AuthSession.id == session_id, AuthSession.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    return True


async def purge_expired(session: AsyncSession) -> int:
    """Delete sessions that have timed out. Expiry is enforced on read regardless; this only
    stops the table growing a row per login for ever."""
    result = await session.execute(
        delete(AuthSession).where(AuthSession.expires_at <= datetime.now(UTC))
    )
    return int(result.rowcount or 0)


async def set_password(session: AsyncSession, user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    user.password_set_at = datetime.now(UTC)
    await session.flush()
    log.info("auth.password_set", user=str(user.id))


def is_claimed(user: User | None) -> bool:
    """Whether this instance has had a password set. An unclaimed instance serves only the auth
    routes — not everything, which is what "no password yet" used to mean."""
    return bool(user and user.password_hash)
