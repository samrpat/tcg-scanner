# Security

What is defended, how, and what is not. The last section is the important one.

## Reporting something

Open an issue for anything that is not itself a vulnerability. For a vulnerability, contact the
maintainer directly rather than in public, and give it a reasonable window before disclosing.

## The front door

One account, one password, chosen at first run — and choosing *not* to have one is a supported
answer, recorded as a decision rather than left as an absence. An instance nobody has set up
serves nothing at all; an instance deliberately left open serves everything and says so, in a
red banner on every screen.

| | |
|---|---|
| Password storage | scrypt, N=16384 r=8 p=1, per-password salt, parameters stored with the hash so they can be raised later |
| Sessions | One database row per device, holding the token's **sha256**, never the token |
| Cookie | HttpOnly, SameSite=Lax, Secure whenever the connection is HTTPS |
| Lifetime | 30 days, revocable per device from Settings |
| Rate limiting | Per address, on login *and* recovery, failing **open** if Redis is down |
| Recovery | A one-time code, ~124 bits, hashed like a password, single-use, replaced on use |

**The gate is middleware, not a per-route dependency.** That is the security property: a
dependency has to be remembered, and two routes were already public because they had no reason
to load a user — `/api/images`, which serves every photograph, and `/api/catalog`. A test
asserts that everything outside `/health` and `/api/auth` refuses without a session, including
routes nobody has written yet.

Refusing a request touches no database. The cheapest request to make against this service must
not be the one that hits Postgres.

## In the browser

A strict Content-Security-Policy, affordable because everything is same-origin — self-hosted
fonts, no analytics, no CDN:

```
script-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'
```

`connect-src 'self'` means that if this application ever starts reporting somewhere, the
browser refuses on your behalf.

Also `Permissions-Policy` granting the camera and denying everything else — geolocation above
all — plus `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` (a URL here
contains a SKU, which is a fact about your collection), and same-origin isolation headers.

**No HSTS**, deliberately. The default certificate is self-signed, and HSTS on a self-signed
host turns "click through the warning once" into "this address is now permanently unreachable
in this browser". Add it behind a real certificate.

## The containers

Non-root, `no-new-privileges`, memory ceilings, log rotation at 10 MB × 3. Postgres and Redis
are not published to the host — the only ports that leave the machine are the web ones.

Redis runs `maxmemory-policy noeviction`: it holds the job queue, and a policy that drops keys
to make room means a scanned card whose processing silently never happens.

## Storage paths

SKUs are the only user-influenced part of a filesystem path. They are validated against
`^CARD-\d{6,}$` before they reach the disk, and the storage layer independently resolves every
path and refuses anything landing outside its root. Two checks, because this is the one place
where getting it wrong reads somebody else's files.

## Publishing photographs

`make photos-on` starts a second nginx and a temporary Cloudflare tunnel, because eBay fetches
listing pictures from URLs at import time. That container:

- serves `GET` and `HEAD` only
- matches exactly one path shape, `^/api/images/(CARD-[0-9]{6})/([a-z0-9-]+\.jpg)$`
- mounts the image directory **read-only**
- has no route to the API, the database, or anything else

It has no password, which is correct — the files it serves are about to be public on a listing
anyway. The application itself is never exposed by it. `make photos-off` is the default state.

## What is not defended

Being specific about this is worth more than the list above.

- **No rate limiting except on login and recovery.** A signed-in client can hammer any
  endpoint.
- **One account.** No roles, no second user, no audit trail of who did what — there is only
  ever one "who".
- **Self-signed TLS by default.** Fine on a LAN. There is no ACME integration.
- **No 2FA.**
- **Sessions do not expire on inactivity**, only at 30 days. A borrowed unlocked laptop is a
  logged-in laptop.
- **The database is not encrypted at rest.** Disk encryption is the operating system's job
  here.
- **Backups contain the `.env`**, including the database password. See `docs/PRIVACY.md`.
- **No dependency scanning in CI.** `pip list --outdated` and `npm audit` are manual.
- **It has never run on a Raspberry Pi**, so none of this has been exercised on the target
  hardware.

## If you expose this to the internet

Mostly: do not. The password is a front door, not a perimeter. Put it behind something that
terminates TLS properly and does its own authentication, and read `docs/DEPLOY.md` first.
