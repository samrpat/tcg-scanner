# Publishing this as a Docker image

How to turn this repository into something a stranger can install, and what has to be true
before you charge anyone for it.

## What someone installing it does

Two files and one command. No checkout, no build, no `make`:

```bash
mkdir tcg-scanner && cd tcg-scanner
curl -O https://raw.githubusercontent.com/samrpat/tcg-scanner/main/docker-compose.release.yml
printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -base64 24)" > .env
docker compose -f docker-compose.release.yml up -d
```

Then `http://localhost:8080`, choose a password, and the introduction explains the rest.

The schema migrates itself on first boot and everything lives in named Docker volumes, so
there is no directory layout to get right and nothing to run by hand. That is deliberate:
every manual step is a step somebody skips and then reports as a bug.

## Building and pushing

```bash
export TCG_IMAGE=ghcr.io/samrpat/tcg-scanner-api
export TCG_WEB_IMAGE=ghcr.io/samrpat/tcg-scanner-web
./scripts/release.sh v0.1.0
./scripts/release-check.sh $TCG_IMAGE:v0.1.0
```

`release.sh` builds **linux/arm64 and linux/amd64** — a Pi and an Apple Silicon Mac need the
first, everything else needs the second, and publishing only the architecture you happen to be
sitting at is the classic way to ship an image half your users cannot run.

`release-check.sh` inspects the image that came out, not the source that went in, because the
things that go wrong here go wrong between the two. It refuses to pass if:

- any card artwork is in the image (see below — this one is legal, not technical)
- the ORB descriptors are missing
- a `.env` was swept in from the build context
- it runs as root
- authentication does not default on
- it has no version label

Run it before every publish. It is the difference between shipping an image and shipping
somebody's database password.

## Before you sell it

I am not a lawyer and none of this is legal advice. These are the things I know are in the
repository that bear on selling it.

### The card-back photograph — handled, keep it handled

`api/app/imaging/assets/pokemon-back.jpg` is a photograph of a Pokémon card back. That artwork
belongs to Nintendo / Creatures / GAME FREAK, and shipping it inside a product you sell is a
different act from keeping it on your own machine.

It is **excluded from built images**. Registration only ever needed the reference's ORB
features and its dimensions, so those are baked into `pokemon-back.npz` and the photograph
stays out — `api/.dockerignore` excludes it and `release-check.sh` verifies it actually did.
Verified bit-identical against six real card backs: same inlier counts, zero corner delta.

A set of binary feature descriptors encodes "there is a corner here, at this scale, with this
gradient pattern". It is not a reproduction and cannot be turned back into one. That is a much
better position than shipping the picture, though it is still worth a lawyer's opinion if real
money is involved.

Run `make bake-reference` if you ever change the photograph.

### Trademarks

Pokémon, and the Pokémon TCG, are trademarks of Nintendo, Creatures Inc. and GAME FREAK inc.
You can make a tool that works with their cards; you cannot imply they made it, endorsed it, or
are otherwise involved. Keep the product name clear of theirs, and say plainly somewhere
visible that it is unaffiliated.

### Redis

Redis 7.4 is **RSALv2 / SSPLv1**, which is source-available rather than open source. Pulling
the official image as a dependency of a compose file the user runs themselves is fine. Offering
this as a **hosted service you operate** is where SSPL starts to matter.

[Valkey](https://valkey.io/) is a BSD-licensed fork and a drop-in replacement — change the
image line and nothing else. If hosting is anywhere in the plan, switch now while it costs one
line.

### Everything else you ship

MIT, BSD, Apache-2.0 and the PostgreSQL licence, all permissive and all fine commercially. The
Ubuntu font is under the Ubuntu Font Licence, which permits redistribution of the unmodified
files. `THIRD_PARTY_LICENSES.md` has the table.

The two AGPL projects in that file — CollectorVision and the_tin — were **reviewed and never
used**. Recognition was built here on OpenCV, Tesseract and perceptual hashing, all permissive.
Nothing copyleft ships. Keep it that way: AGPL reaches network use, which for a hosted product
means publishing your source.

### Card data and prices

TCGdex is MIT for both code and data. Prices originate with TCGplayer and Cardmarket and are
redistributed by TCGdex; they are stored with their source and timestamp and shown, not resold
as a price feed. Selling a *scanner* that displays them is a different proposition from selling
a *pricing product*, and the second one needs its own look.

### There is no LICENSE file

This repository has no licence, which means nobody has permission to use it. That is the right
default for something you are thinking about selling — but it has to be a decision. Add a
`LICENSE` before publishing: proprietary terms if you are charging, or an open licence if you
would rather people build on it. The Dockerfile label already points at the file.

## What a paying user would notice is missing

Being honest about this is cheaper than finding out from a refund request.

- **One account per install.** The schema carries `user_id` everywhere, but there is no way to
  add a second person, and no roles.
- **It has never run on a Raspberry Pi.** Every measurement in this repository is from an M3
  Mac under Colima. The Pi is the target and the instructions are right, but the timings are
  unknown and the memory ceilings are reasoned rather than measured.
- **Self-signed TLS only.** Fine on a LAN; there is no Let's Encrypt integration.
- **No update mechanism.** `docker compose pull && up -d`, by hand, and they have to know.
- **No import.** An existing collection cannot be brought in; everything starts from scanning.
- **Backups are local by default.** `make backup` writes to the same disk. Anyone relying on
  this needs telling to copy them elsewhere.
- **English cards only**, and the card-back reference is the English back.

## Versioning

`scripts/release.sh v0.1.0` tags both images and stamps `TCG_VERSION` into the API, which
surfaces in Settings → This install. A user reporting a problem can read their version off the
screen, which is worth more than it sounds.
