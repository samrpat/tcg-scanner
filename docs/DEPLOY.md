# Deploying this for real

Written for a Raspberry Pi 5 on a home network, which is what it is for. Everything here works
the same on any Linux box with Docker.

**This has not yet been run on a Pi.** Every measurement in the repo comes from an M3 Mac under
Colima. The instructions are right; the timings are not known.

## Before anything else

The one thing that has to be true: **this application holds photographs of your collection and
can delete all of them, and until recently it had no password on it.** It has one now. Set it.

## First run

```bash
git clone <your copy>          # or copy the directory over
cd tcgscanner
cp .env.example .env
```

Edit `.env` and set `POSTGRES_PASSWORD` to something long and random:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(24))'
chmod 600 .env
```

Then:

```bash
make up          # builds and starts: postgres, redis, api, worker, web
make migrate     # creates the schema
```

Open `http://<pi>:8080`. It will ask you to **choose a password** — the instance is unclaimed
until you do, and until then it serves nothing but the login screen. Pick a phrase you can type
on a phone; the minimum is ten characters and length beats punctuation.

That is the whole install for scanner mode. Identification and pricing need reference data as
well:

```bash
make full        # the whole application instead of the scanner
make seed
make sync        # ~20k cards, 218 sets. Takes a while.
make prices
```

## The camera needs HTTPS

A browser will not give a page the camera unless it is on `localhost` or HTTPS. Scanning from a
phone therefore needs a certificate:

```bash
make cert
```

This writes a self-signed certificate to `data/certs` and the app serves HTTPS on 8443. The
phone will warn about the certificate once; accept it and it stays accepted.

The session cookie is marked `Secure` only on HTTPS connections, so it works on both addresses.

## Backups

`make backup` writes the database, the images and a copy of `.env` to `data/backups/<stamp>/`,
with a `RESTORE.md` beside them. It keeps the newest seven and prunes the rest — each one holds
a full copy of the images, so without pruning this fills the disk, and on a Pi the disk is the
SD card the database lives on.

Run it nightly. As a systemd timer:

```ini
# /etc/systemd/system/tcg-backup.service
[Unit]
Description=TCG Scanner backup
[Service]
Type=oneshot
WorkingDirectory=/home/pi/tcgscanner
ExecStart=/usr/bin/make backup
User=pi
```

```ini
# /etc/systemd/system/tcg-backup.timer
[Unit]
Description=Nightly TCG Scanner backup
[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true
[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now tcg-backup.timer
```

**A backup on the same SD card as the thing it is backing up is not a backup.** Copy
`data/backups` somewhere else — another machine, an external disk — on whatever schedule you
would actually miss the data over.

Restoring is in the `RESTORE.md` inside each backup.

## Starting on boot

The containers are `restart: unless-stopped`, so they come back when Docker does. Make Docker
come back:

```bash
sudo systemctl enable docker
```

`make off` stops them deliberately, and deliberately stopped containers stay stopped across a
reboot. `make on` starts them again.

## What is hardened, and what is not

Each container has a memory ceiling, `no-new-privileges`, and log rotation at 10 MB × 3 —
without that last one, months of request logs fill the card.

Postgres and Redis are **not** published to the host. The only ports that leave the machine are
the web ones, 8080 and 8443.

Redis runs `maxmemory-policy noeviction` on purpose: it holds the job queue, and a policy that
drops keys to make room means a scanned card whose processing job silently vanishes.

### Still open

- **No rate limit except on login.** A logged-in client can hammer any endpoint.
- **One user.** The schema carries `user_id` everywhere and the login is single-account; there
  is no way to add a second person.
- **Self-signed TLS only.** Fine on a LAN, not for anything public.
- **The photo host is deliberately unauthenticated** when you turn it on — see below.

## Exposing it to the internet

Mostly: do not. There is one reason to, and it is narrow.

eBay fetches listing photographs from URLs at import time, so a bulk upload can only carry
photos if the images are reachable from outside. `make photos-on` starts a second nginx that
serves **the JPEGs and nothing else** — read-only mount, one path shape, GET and HEAD only, no
API — behind a temporary Cloudflare tunnel, and `make photos-off` takes it down again.

That container has no password, which is correct: the files it serves are about to be public on
a listing anyway. The application itself is never exposed by it.

If you want the app itself reachable from outside, put it behind something that terminates TLS
properly and does its own authentication. The password here is a front door, not a perimeter.

## Updating

```bash
make backup
git pull
make up          # rebuilds and restarts
make migrate
make smoke       # TCG_PASSWORD=... make smoke
```

`make smoke` exercises every endpoint against the running stack and is the fastest way to know
a deploy did not break a screen.

## When something is wrong

```bash
make status      # what is running, and in which mode
make health      # per-dependency, with timings
make logs        # all of it, following
```

`/health` answers without a password, on purpose, so a monitor does not need a credential. It
reports whether the process is alive and its dependencies answer, and nothing about the
collection.
