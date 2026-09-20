"""The front door.

This application ran for months with no authentication at all — every endpoint open to
anything that could reach the port, including the ones that delete inventory and the one that
publishes photographs to the internet. These tests exist to keep that from coming back by
accident.
"""

import inspect

import pytest
from fastapi.testclient import TestClient

from app.main import build_app
from app.services import auth as auth_service

# ── passwords ──────────────────────────────────────────────────────────────────────────────


def test_a_password_round_trips():
    encoded = auth_service.hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert auth_service.verify_password("correct horse battery staple", encoded)
    assert not auth_service.verify_password("Correct horse battery staple", encoded)
    assert not auth_service.verify_password("", encoded)


def test_the_same_password_hashes_differently_every_time():
    """A per-password salt. Without it, two accounts with the same password are visibly the
    same in the table, and one cracked hash is every matching account."""
    a = auth_service.hash_password("correct horse battery staple")
    b = auth_service.hash_password("correct horse battery staple")
    assert a != b
    assert auth_service.verify_password("correct horse battery staple", a)
    assert auth_service.verify_password("correct horse battery staple", b)


def test_short_passwords_are_refused():
    with pytest.raises(auth_service.AuthError):
        auth_service.hash_password("pokemon")


@pytest.mark.parametrize(
    "stored",
    [None, "", "notahash", "scrypt$broken", "bcrypt$2b$12$whatever", "scrypt$x$y$z$q$r"],
)
def test_a_malformed_hash_reads_as_a_wrong_password(stored):
    """Never a 500. A corrupt hash column must look exactly like the wrong password, or it
    tells whoever is probing that they have found something interesting."""
    assert auth_service.verify_password("anything at all", stored) is False


def test_the_stored_hash_contains_its_own_parameters():
    """So the cost can be raised later without invalidating the password anybody already has."""
    encoded = auth_service.hash_password("correct horse battery staple")
    scheme, n, r, p, _salt, _key = encoded.split("$")
    assert scheme == "scrypt"
    assert int(n) >= 2**14 and int(r) >= 8 and int(p) >= 1


def test_the_token_is_never_stored_only_its_hash():
    """A backup of this database leaks to disk by default — `make backup` writes one. It must
    not be a set of working cookies."""
    source = inspect.getsource(auth_service.issue_session)
    assert "token_hash=token_hash(token)" in source
    assert auth_service.token_hash("abc") != "abc"
    assert len(auth_service.token_hash("abc")) == 64


# ── the gate ───────────────────────────────────────────────────────────────────────────────


def test_authentication_is_on_unless_something_turns_it_off():
    """The default is the whole point. Everything else here is about the door working; this is
    about the door existing."""
    from app.config import Settings

    assert Settings().auth_required is True


def test_nothing_but_health_and_auth_answers_without_a_session():
    """Middleware, not a per-route dependency, precisely so this can be asserted for routes
    nobody thought about. Two were already public by omission — `/api/images`, which serves
    every photograph of every card, and `/api/catalog`.
    """
    client = TestClient(build_app(scanner=False, auth_required=True))

    assert client.get("/health").status_code == 200
    assert client.get("/api/auth/status").status_code == 200

    for path in (
        "/api/inventory",
        "/api/sessions",
        "/api/capture/recent",
        "/api/images/CARD-000001/listing-front.jpg",
        "/api/catalog/stats",
        "/api/jobs",
        "/api/ebay/queue",
        "/api/docs",
    ):
        assert client.get(path).status_code == 401, f"{path} answered without a session"


def test_refusing_a_request_never_touches_the_database():
    """The cheapest request to make against this service must not be the one that hits
    Postgres — otherwise an unauthenticated flood is a denial of service, and a database
    outage turns every 401 into a 500.

    Asserted by refusing with no database reachable at all: these tests have none.
    """
    client = TestClient(build_app(scanner=True, auth_required=True))
    response = client.get("/api/inventory")
    assert response.status_code == 401
    assert response.json()["reason"] == "unauthenticated"


def test_turning_it_off_lets_everything_through():
    """The escape hatch works. `/api/docs` is the probe because it needs no database — what is
    being tested is the gate, not what is behind it."""
    on = TestClient(build_app(scanner=True, auth_required=True))
    off = TestClient(build_app(scanner=True, auth_required=False))
    assert on.get("/api/docs").status_code == 401
    assert off.get("/api/docs").status_code == 200


def test_the_open_list_is_short_and_deliberate():
    """Anything added here is public. It should be hard to do by accident."""
    from app.authz import OPEN_PREFIXES

    assert set(OPEN_PREFIXES) == {"/health", "/api/auth"}
    # A prefix must not match a longer sibling: "/api/authority" is not "/api/auth".
    from app.authz import is_open

    assert is_open("/api/auth/login")
    assert is_open("/health")
    assert not is_open("/api/authorised-only")
    assert not is_open("/api/inventory")


def test_the_session_cache_can_be_dropped():
    """Revoking a device has to take effect now, not when a cache entry ages out."""
    from app import authz

    authz._CACHE["tok"] = ("user", 9e9)
    authz.forget("tok")
    assert "tok" not in authz._CACHE

    authz._CACHE["a"] = ("user", 9e9)
    authz._CACHE["b"] = ("user", 9e9)
    authz.forget_all()
    assert authz._CACHE == {}


def test_claiming_is_refused_once_a_password_exists():
    """Otherwise it is a way to take over an instance without knowing the current password."""
    from app.routers.auth import claim

    source = inspect.getsource(claim)
    assert "is_claimed" in source
    assert "409" in source


def test_changing_the_password_signs_the_other_devices_out():
    """A password change that leaves the device you are worried about still logged in has not
    done anything."""
    from app.routers.auth import change_password

    source = inspect.getsource(change_password)
    assert "revoke_all" in source
    assert "keep=keep" in source  # ...but not the one making the request


def test_login_is_rate_limited_and_fails_open():
    """Locking the operator out of their own scanner because Redis hiccupped trades a real
    outage for a theoretical one."""
    from app.routers.auth import _too_many, login

    assert "_too_many" in inspect.getsource(login)
    assert "return False" in inspect.getsource(_too_many)


def test_the_cookie_is_httponly_and_lax():
    from app.routers.auth import _set_cookie

    source = inspect.getsource(_set_cookie)
    assert "httponly=True" in source
    assert 'samesite="lax"' in source
    # Secure only over TLS: setting it on the plain-HTTP LAN address means the browser never
    # sends the cookie back, which presents as "logging in does nothing".
    assert 'secure=request.url.scheme == "https"' in source


# ── settings ───────────────────────────────────────────────────────────────────────────────


def test_settings_are_behind_the_gate_like_everything_else():
    from app.main import build_app

    client = TestClient(build_app(scanner=True, auth_required=True))
    assert client.get("/api/settings").status_code == 401


def test_only_named_preferences_can_be_stored():
    """This column is written straight from the browser. An allowlist rather than a blob,
    because "whatever the client sent" is how a settings object becomes somewhere to stash
    unbounded junk — in a JSONB column on the user row, no less."""
    from app.routers.preferences import ALLOWED, DEFAULTS, PreferencesIn

    assert set(ALLOWED) == set(DEFAULTS)
    # Anything not named is dropped by the model before it reaches the writer.
    parsed = PreferencesIn.model_validate({"intro_done": True, "sneaky": {"a": [1] * 1000}})
    assert not hasattr(parsed, "sneaky")
    assert parsed.intro_done is True


def test_configuration_is_reported_with_where_it_is_set():
    """A settings screen that appears to offer a switch it cannot throw is worse than one that
    says the switch is in a file."""
    import inspect

    from app.routers.preferences import read_preferences

    source = inspect.getsource(read_preferences)
    assert "mode_source" in source
    assert "authentication_source" in source
    assert ".env" in source


# ── the choice, and the way back ───────────────────────────────────────────────────────────


def test_no_password_is_a_stored_decision_not_an_empty_field():
    """"Nobody has set this up yet" and "the operator decided against a password" look the
    same from outside and must not behave the same. The first serves nothing; the second
    serves everything. A column records which."""
    import inspect

    from app.authz import refresh_instance_flag
    from app.routers.auth import claim

    assert "auth_disabled = True" in inspect.getsource(claim)
    # And an unclaimed instance is never read as an open one.
    source = inspect.getsource(refresh_instance_flag)
    assert "user and user.auth_disabled" in source
    assert "_OPEN_INSTANCE = False" in source  # a database that cannot answer is not a yes


def test_the_open_flag_is_never_read_on_the_request_path():
    """It was, briefly, and it put a query back on the refusal path that D-177 had removed —
    then failed *open* when the query misbehaved, which is the wrong direction for a gate.

    It is read at startup and re-read by whoever changes it. The request path reads a
    variable.
    """
    import inspect

    from app import authz

    assert not inspect.iscoroutinefunction(authz.instance_is_open)
    source = inspect.getsource(authz.instance_is_open)
    assert "new_session" not in source
    assert "select" not in source

    # Unknown is closed.
    authz._OPEN_INSTANCE = None
    assert authz.instance_is_open() is False
    authz._OPEN_INSTANCE = True
    assert authz.instance_is_open() is True
    authz._OPEN_INSTANCE = False


def test_the_recovery_code_is_hashed_like_a_password():
    """A stolen database should give the same answer for every credential in it — nothing
    usable — and the way to guarantee that is one way of storing a secret, not two."""
    code = auth_service.new_recovery_code()
    stored = auth_service.hash_recovery(code)
    assert stored.startswith("scrypt$")
    assert code not in stored
    assert auth_service.verify_recovery(code, stored)


def test_a_recovery_code_survives_being_typed_by_a_human():
    """It gets written on paper and read back by somebody already having a bad day."""
    code = auth_service.new_recovery_code()
    stored = auth_service.hash_recovery(code)
    for typed in (code.lower(), code.replace("-", " "), code.replace("-", ""),
                  f"  {code.lower().replace('-', ' ')}  "):
        assert auth_service.verify_recovery(typed, stored), typed
    assert not auth_service.verify_recovery("AAAAA-BBBBB-CCCCC-DDDDD-EEEEE", stored)


def test_recovery_codes_avoid_the_ambiguous_characters():
    """0/O and 1/I/L are the reason a written-down code fails to work."""
    for _ in range(20):
        assert not (set(auth_service.new_recovery_code()) & set("01OIL"))


def test_recovery_is_rate_limited_and_single_use():
    """A code is the password's equal, so guessing at one must cost the same as guessing at
    the other — and a used code is spent."""
    import inspect

    from app.routers.auth import recover

    source = inspect.getsource(recover)
    assert "_too_many" in source
    assert "issue_recovery_code" in source  # a replacement is issued, so the old one is dead
    assert "revoke_all" in source  # recovery means access was lost; sign everything out


def test_turning_the_password_on_later_closes_the_instance():
    import inspect

    from app.routers.auth import require_password

    source = inspect.getsource(require_password)
    assert "auth_disabled = False" in source
    assert "issue_recovery_code" in source
    assert "forget_all" in source  # the cached "this instance is open" must not linger
