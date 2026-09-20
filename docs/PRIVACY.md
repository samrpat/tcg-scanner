# What this sends, stores, and gives away

Written to be checked rather than believed. Every claim here has a way to verify it, and the
commands are included.

## What leaves the machine

**In scanner mode — the default — nothing.**

Verified by looking at the connections the API actually holds while the app is in use:

```bash
docker compose exec api python -c "
import socket
for line in open('/proc/net/tcp').read().splitlines()[1:]:
    f = line.split()
    if f[3] == '01':
        ip, port = f[2].split(':')
        print(socket.inet_ntoa(bytes.fromhex(ip)[::-1]), int(port, 16))"
```

The only connection is to Postgres on the container network. There is no analytics, no crash
reporting, no update check, no licence check, and no font CDN — the Ubuntu typeface is served
from the same origin as everything else, so not even a font request tells anybody you opened
this.

Full mode adds three, all of them optional and all of them things you turn on:

| Destination | When | Why | Turn it off |
|---|---|---|---|
| `api.tcgdex.net` | `make sync`, `make prices` | Card names, sets, variants, prices | Don't run them, or self-host TCGdex (`--profile tcgdex`) |
| `api.apify.com` | Pulling real eBay sold prices | Comparable sales | Leave `APIFY_TOKEN` unset — with no token it never runs |
| Cloudflare | `make photos-on` only | A temporary public address so eBay can fetch listing photographs | `make photos-off`, which is also the default state |

Check for yourself what the code can reach:

```bash
grep -rhoE "https?://[a-zA-Z0-9.-]+" api/app | sort -u
```

## Photographs

They stay on your disk. `data/images`, or a bucket you configured yourself. Nothing uploads
them anywhere, and the only way one becomes publicly readable is `make photos-on`, which you
run deliberately and which serves JPEGs and nothing else.

### Metadata is removed before anything is written

A photograph from a phone carries where it was taken, on what, and when. This application has
two ways of handing that to a stranger — the batch export offers the originals under
`kind=all`, and the public photo host serves them by URL — so it comes off on the way in.

Captures through the app's own camera never had any: a browser canvas has nothing to give.
**Uploads did**, and that was a real hole until it was closed.

What is removed: GPS, camera make and model, serial numbers, timestamps, software, XMP, IPTC.
What is kept: the orientation tag, the colour profile, and every pixel — the scrub edits the
JPEG's metadata segments and leaves the compressed image untouched, so nothing is re-encoded
and no quality is lost.

Check a stored original:

```bash
docker compose exec api python -c "
from PIL import Image, ExifTags
import glob
for p in sorted(glob.glob('/data/images/*/original-*.jpg'))[:5]:
    ex = Image.open(p).getexif()
    print(p, {ExifTags.TAGS.get(k, k) for k in ex} or 'no metadata', 'GPS' if ex.get_ifd(0x8825) else '')"
```

If you have images from before this existed, `make scrub-metadata` cleans them —
`--dry-run` first to see what it would do.

## What is logged

Structured application logs: what was scanned, which jobs ran, how long things took. Plus the
client IP on a **failed** login, which is what makes rate limiting possible.

Logs are not sent anywhere. They rotate at 10 MB × 3 per container and are visible with
`make logs`. No card image, no password, and no session token is ever written to them — the
token is hashed the moment it arrives.

## What is stored, and where

| | |
|---|---|
| Photographs | `data/images`, on your disk |
| Card records, batches | Postgres, in a Docker volume, not published to the host |
| Password | Hashed with scrypt. The plaintext is never stored |
| Recovery code | Hashed the same way, for the same reason |
| Sessions | One row per signed-in device, holding a **hash** of the token — a leaked database backup is not a set of working cookies |

## Backups contain secrets

`make backup` copies `.env` alongside the database, because a restore without it is not a
restore. That file has your database password in it.

The backup directory is `chmod`-protected and gitignored, and the point stands: **a backup of
this is as sensitive as the thing itself.** Treat it that way when you copy it somewhere else,
which you should, because a backup on the same disk is not a backup.

## What your customers should know

If you distribute this, the honest summary for whoever installs it:

- It runs entirely on their hardware and talks to nothing by default.
- Their photographs never leave their disk unless they publish them on purpose.
- It has a password, and they choose whether to use one.
- Nobody — including you — can see their collection.

All four are checkable with the commands above, which is the difference between a privacy
policy and a privacy claim.
