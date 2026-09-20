# Changelog

## 2026-09-20 (download fix)

- **Fixed: the photo download returned 502.** The endpoint built the whole archive in memory —
  two gigabytes for a 110-card batch, inside a container limited to one — so requesting it
  OOM-killed the API rather than failing the download. It streams now: peak memory **108 MB**
  regardless of batch size, measured at 1.06 GB in 15.7 seconds on the batch that was failing.
  See D-182.

## 2026-09-20 (fix)

- **Typing a section name on the Scan screen took a photograph on every space.** The shutter's
  Space/Enter binding had no check for whether you were typing, and because it calls
  `preventDefault` the space never reached the field either — the name was impossible to type.
  Enter shot a card instead of submitting it. Both screens now share one guard, and the
  shutter is inert while the section sheet is open. See D-181.

## 2026-09-20 (sections, properly)

- **Sections are drawn as panels**, not headings over a continuous stream — bordered, with a
  header strip, and an accent border on the one new scans land in. They fold away, and stay
  folded. See D-180.
- **The current section shows even when empty**, which is the moment just after you create
  one and exactly when you need to see where the next card is going.
- **Set the section from the Scan screen.** A pill well away from the shutter opens a sheet:
  name the next pile and press Enter, or tap an existing one. Takes effect on the next
  shutter press, with no trip to another tab.
- Where scans land is now its own fact, separate from the order sections are displayed in —
  so you can go back to an earlier pile without reordering anything.

## 2026-09-20 (later)

### Sections within a batch
- A batch can be divided, and new scans land in the current division. Sort the pile into
  reverse holos, normals and unknowns, then by condition, and the screen keeps that shape.
  See D-178.
- **+ section** starts one; headings rename in place; **remove section** takes the heading and
  never the cards.
- The move dropdown now offers sections of this batch alongside other batches — one question,
  "where do these go?", rather than two controls.
- **a folder per section** in the download, composing with the photo-count split:
  `01 Reverse holo - NM/7-photos-per-card/…`. Folders numbered so they sort in scanning order.

### Fixed
- **A gate that failed open.** The new open/closed instance flag was read from the database on
  every refusal, and let requests through when that read misbehaved — four endpoints answered
  200 with no session. It is read at startup now, the request path reads a variable, and
  unknown means closed. See D-179.

## 2026-09-20

### Privacy
- **Photographs are stripped of their metadata before they are stored.** A file uploaded from
  a camera roll carried GPS, camera model and timestamp, and both the batch export and the
  public photo host would have served it. Lossless — the metadata segments come out and the
  pixels are bit-identical. See D-175.
- `make scrub-metadata` cleans images stored before this existed. All 94 here have been done.
- `docs/PRIVACY.md` — what leaves the machine (in scanner mode: nothing, and the command to
  verify it), what is logged, and what a backup contains.

### Security
- **A password is now a question at setup, not an environment variable** — with a third state
  the code takes seriously: unclaimed serves nothing, open-by-choice serves everything and
  says so in a banner. Reversible from Settings. See D-176.
- **Recovery codes.** Shown once at setup, ~124 bits, no ambiguous characters, typed back in
  any case with or without dashes. Single-use, replaced on use, and using one signs every
  device out.
- Content-Security-Policy, Permissions-Policy (camera yes, location no), `nosniff`,
  `X-Frame-Options`, `Referrer-Policy: no-referrer`, and same-origin isolation. No HSTS, on
  purpose — it would brick a self-signed host. See D-177.
- `docs/SECURITY.md`, including an explicit list of what is *not* defended.

### Fixed
- **A 401 feedback loop.** Dashboard polls ran behind the login screen and each 401 asked the
  gate to re-check, turning one burst into nine. Zero requests on the login screen now.
- **`index.html` was cacheable**, so a browser could keep serving last month's app after an
  update, with no way to tell. Now `no-cache`, with fingerprinted assets still `immutable`.

## 2026-09-19 (later still)

### Less work when nobody is looking
- Every screen polled at full rate in a **background tab** — about **2,160 requests an hour**
  into a tab with no reader. Polling now pauses when the tab is hidden and refreshes the
  moment it comes back. Measured: **0 requests in 31 seconds hidden.** See D-173.
- The batch screen refetched the photo-count plan on every six-second tick. It now fetches it
  only when the cards it describes change — a third of that screen's traffic, gone.
- Everything else was measured and left alone: endpoints are 4–13 ms, detection is ~400 ms per
  card against a 36-second budget, thumbnails were already cached `immutable`, and the image
  is near the floor for Python + OpenCV. The one apparent regression — a filtered inventory
  query looking 3× slower — turned out to be noise at five samples.

### Easier to pick up
- The footer paragraph explaining all four tabs is gone. It was never next to the thing it
  explained, never mentioned Extras, and ended by telling the reader to edit an environment
  variable they cannot reach. Each screen now says what it is for, in one line, at the top.
  See D-174.
- "The camera needs HTTPS — run `make cert`" now names **the address to open** instead, with
  the port read from the health check. A link is a useful answer; a command somebody cannot
  run is not.

## 2026-09-19 (later)

### It can be handed to someone else now
- **An introduction on first run** — four steps, skippable, explaining what the tool is for
  before how it works. Progress is stored on the account, so dismissing it on the desktop also
  dismisses it on the phone. See D-172.
- **Settings behind a gear**, not a tab: version, mode, collection size, password change,
  signed-in devices, and a way to replay the introduction. Configuration that lives in `.env`
  is shown read-only with a note saying so, rather than as a switch that does nothing.
- Fixed: `.spacer` was scoped to one component, so the same markup in the batch bar had never
  pushed anything anywhere.

### Ready to publish as a Docker image
- `docker-compose.release.yml` — pulls published images, named volumes, **no checkout and no
  `make`**. An install is that file, a password, and `docker compose up -d`. See D-171.
- **Migrations run themselves** on API start. A first run that comes up and then 500s because
  no tables exist looks like it worked, which makes it the worst kind of broken.
- `scripts/release.sh` builds **arm64 and amd64** and pushes both.
- `scripts/release-check.sh` inspects the built image and refuses to pass it if there is card
  artwork in it, a `.env` swept in from the build context, a root user, authentication not
  defaulting on, or no version label.
- `docs/DISTRIBUTION.md` — how to publish, the licensing position, and an honest list of what
  a paying user would find missing.

### The card back no longer ships as artwork
- `pokemon-back.jpg` is Nintendo / Creatures / GAME FREAK's, and it was baked into the image.
  Registration only ever needed its ORB features and its dimensions, so those are baked to
  `pokemon-back.npz` and the photograph is excluded from builds. **Verified bit-identical**
  across six real card backs — same inlier counts, zero corner delta. See D-170.

## 2026-09-19

### This now has a password on it
- **Every endpoint was open to anything that could reach the port** — the inventory, every
  photograph, the endpoints that delete cards. Two of them were public even to the UI's own
  reckoning: `/api/images` and `/api/catalog` had no reason to load a user, so they never asked
  for one. There is a login now, and the gate is middleware rather than a per-route dependency
  precisely so a route cannot be forgotten. See D-168.
- Open the app and it asks you to **choose a password**. Until you do, it serves nothing but
  that screen — "no password set" is a locked state, not an open one.
- Signed in for 30 days per device. **Devices are listed and revocable**, so a lost phone can
  be cut off on its own. `make sign-out-all` cuts off everything.
- `make set-password` is the way back in if it is forgotten.
- Login is rate limited per address, and fails open if Redis is down — locking you out of your
  own scanner because the queue hiccupped trades a real outage for a theoretical one.
- `AUTH_REQUIRED=false` still exists for a network you control completely. It logs a warning,
  shows in `/health/detail`, and puts a red banner across every screen.

### Ubuntu
- The whole interface is Ubuntu, with Ubuntu Mono for SKUs and code. **Self-hosted**, 88 KB,
  latin subset — the Pi may have no route to the internet, and type that only arrives when a
  CDN is reachable is type that vanishes on a bad day. See D-167.

### Ready to leave running
- **Log rotation** at 10 MB × 3 per container. Docker's default is unbounded, and this runs on
  a Pi whose SD card also holds the database.
- **Memory ceilings** per container, and `no-new-privileges` on all of them.
- Redis runs `noeviction`: it holds the job queue, and a policy that drops keys to make room
  means a scanned card whose processing job silently disappears.
- **`make backup` keeps the newest seven** and prunes the rest. Each one copies every image;
  there were already 1.7 GB of them and nothing was deleting any.
- `docs/DEPLOY.md` — first run, HTTPS for the phone camera, a systemd timer for backups,
  updating, and an honest list of what is still open.

## 2026-09-12

### Folders by photographs-per-card
- **split by photo count** in the Batch bar puts each count in its own folder —
  `6-photos-per-card/`, `7-photos-per-card/` — named as the number to type into the uploader.
  Upload each separately and every card gets its own photographs. See D-166.
- The Batch bar now shows the breakdown **before** you download, and turns amber when a batch
  holds more than one count. A bulk uploader chunks a flat folder by a fixed photo count, so one
  card with a seventh photograph shifts every card after it — and the result is a listing
  illustrated with somebody else's card.
- Cards with no photographs are named explicitly rather than quietly left out of the download.

### Angled shots are now extra shots
- Same three slots, same behaviour, broader name: a holo tilted so the foil reads, a crease
  close up, a signature, the edge of something thick. Naming them after the first use narrowed
  what anyone would think to put in them. See D-165.
- The **Angles** tab is now **Extras**. Migration 0023 renames the enum values, rewrites the
  paths and moves the files; the six shots already stored came through intact.

### The angle pass

- **Angles** is a tab now. It walks a batch one card at a time — the card's own scan on screen
  so you know which to pick up — and offers Capture angle (space) or Skip (S). Skip is the
  answer on most cards, so it is the same size as capture and has the cheaper key. See D-163.
- **only cards without one** is on by default, so a card you have shot drops out of the queue
  and coming back resumes where the work is. A skip is not remembered — passed-over cards are
  offered again next time.
- Runs over any batch, archived ones included.

### Angled shots, for holos
- A card can carry up to three extra photographs taken by hand at an angle, and they are
  **never rectified**. A holo only looks like a holo when light rakes across it; every other
  image here is deliberately flat, which is exactly the angle that makes an expensive holo
  render as matte. See D-161.
- On the phone: **✦ angle shot** under the shutter. It attaches to the card you are on, names
  the card it landed on, and does not touch the front/back rotation.
- In **Batch**: open a card and use **capture an angle shot** for a camera inline, or **or
  upload one** for a file. Remove them individually from the same place. The camera runs only
  while the panel is open.
- They sort third in the download folder, ahead of the corner close-ups — on a holo that is the
  photograph that sells the card.
- Orientation is baked in, the long edge capped at 3920, re-encoded at q97, and **all metadata
  is dropped**. These are the only images that reach a listing without passing through the
  pipeline, so they are the only ones that could have uploaded a phone's EXIF — including, on
  most phones, where the photograph was taken.

### Batches can be renamed
- Click the name — in the bar or under **Earlier batches** — and type. Enter saves, Escape
  abandons, an empty name restores the generated one. See D-162.
- The restored name comes from the batch's own start date, not today's.

### Changed
- Photograph filenames are numbered **as they are written** rather than by a fixed slot per
  kind, so a card with no angled shots is numbered 1–6 with no gap and a card with one is
  numbered 1–7 with the angle at 3. Previously a fixed scheme would have left a gap on every
  card without extras — which reads as a missing photograph.

### Fixed
- Renaming with the keyboard sent the request twice: Enter unmounts the input, and unmounting
  fires blur, which was also wired to save.

## 2026-09-11

### Corner close-ups are made again, and the switch means something
- The **corner close-ups** switch was a browser preference that only decided what went into a
  download. Nothing ever cut a corner on its own, so a batch scanned with it on still had none:
  **twelve of eighteen cards had no corner images at all.** The setting now lives on the batch,
  and the worker cuts the four crops as each card finishes processing. See D-159.
- The switch shows on an empty batch too, so the next pile can be set up before the first card.
- Turning it **off deletes nothing** — it stops new ones being cut. Removing the files is a
  separate, deliberate click.
- **cut any missing** fills gaps without re-cutting what is there: a card moved in from a batch
  that had the switch off, one whose crop failed, or anything scanned before this existed.
- The download now follows the batch's own setting instead of always including corners.
- The setting is inherited when you archive and start the next pile.

### Fixed
- **Every corner endpoint was 404ing in scanner mode.** They lived on the eBay router, which
  scanner mode stopped mounting — so the card editor's corner slider and the remove button, on
  the one screen that is entirely about corner crops, called routes that did not exist. Per-card
  re-cutting moved to `/api/capture/{sku}/detail-shots`; batch-level building and deleting are on
  the sessions router. See D-160.

## 2026-09-08

### The scanner is now its own build
- `make up` runs the scanner and nothing else: five containers, no tesseract, and the listing
  routers not mounted. `make full` layers everything back on — identification, conditioning,
  pricing, eBay titles and upload files, the public photo host — against the same database, the
  same photographs and the same batches. Switching is a restart, never a migration. See D-152.
- The listing half is **unreachable** in scanner mode, not merely hidden. This app has no
  authentication, so a mounted route is an open route, and that half includes deleting inventory
  and publishing photographs to the internet. A test asserts both directions. See D-153.
- Scanner image **738 MB → 608 MB**: tesseract is only ever called to read a collector number,
  which is identification, so it is a build argument now. Also an apt step skipped on every Pi
  rebuild. See D-154.
- A frozen copy of the tree as it stood before the split is in `archive/full-2026-09-08/`, for
  reading and diffing. It is deliberately not a second installation — there is one database and
  one set of photographs, and they stay where they are.

### Less work for nothing
- The dashboard's three feeds polled for as long as the tab was open, whichever screen was
  showing — a database query every four seconds, drawn nowhere, on a screen that does not exist
  in scanner mode. They now run only while the dashboard is on screen. See D-155.
- nginx gzips text and caches Vite's fingerprinted assets for a year. The JS bundle and the
  batch's JSON compress by roughly three quarters.
- The Scan strip fetches its previews at `?w=240` instead of pulling the full 2900 px render.

### Starting and stopping
- `make on` / `make off` start and stop **the Docker VM as well as the containers**, so a laptop
  on battery between scanning sessions has nothing of this running on it. No-ops on a Pi.
  `make status` says what is running and in which mode. See D-156.
- `make up` no longer reports "unreachable" at a stack that is three seconds from ready: the
  health check waits up to a minute for a cold API.

### Batches
- **Cards can be moved between batches.** Tick `move` on any cards and pick a destination —
  an existing batch, or a new one, which is created closed so that making somewhere to put cards
  does not redirect the scanner into it mid-pile. Only the grouping changes; no photograph is
  touched. See D-157.

### Fixed
- The Scan strip said *identifying…* for ever on a photos-only batch, where no name is ever
  coming. It now shows the front and the back of the card just shot, with a card-shaped
  placeholder for a side still processing. See D-158.
- Removed a stale paragraph on the main screen that described Approve, Inventory and pricing in
  scanner mode, where none of those screens exist.

## 2026-09-04

### Fixed: processing jobs were not lost, they were deadlocking
- The two sides of a card are processed concurrently, and both write rows belonging to the same
  inventory item. That insert takes a `FOR KEY SHARE` lock on the parent row, and the review
  reconcile then asked for `FOR UPDATE` on the same row — a lock upgrade, from two transactions
  at once. Postgres killed one, and **that side's processed image was silently never written**.
  This is the long-standing "3 of 30 jobs lost" symptom. The item is now locked before the first
  child write. Measured: 12 of 12 sides processed across six concurrent scans, zero deadlocks.
  See D-119.

### eBay
- The upload file creates **drafts** (`Action=Draft`), not live listings. Confirmed against
  eBay's own documentation. See D-118.
- **Photographs are attached automatically**: `make photos-on` starts a photos-only host and a
  Cloudflare tunnel, and the export reads the public address from the tunnel itself rather than
  asking anyone to copy a hostname that changes on every restart. Six photos per card. Verified
  end to end through Cloudflare's edge: 200 OK on every image, 404 on every app path. See D-117.
- **Corner close-ups**, four separate JPEGs per card, cut from the listing render so each keeps
  real background beyond the card edge, overlapping 4% so seam wear is whole in both. See D-115.
- Item specifics are generated in full — Game, Set, Card Name, Card Number, Rarity, Finish,
  Language, Manufacturer, HP, Stage, Card Type, Illustrator, Features, Country of Origin — taken
  from real sold listings rather than guessed. See D-114.
- **Descriptions are per-card**, built from that card's own catalogue entry: typing, stage, HP,
  ability and attack text in eBay's `[C][C][C]` notation, weakness, resistance, retreat.
- **Fixed two false claims in descriptions.** The old text promised "a penny sleeve and toploader
  inside a rigid mailer" on every listing, which is untrue of an eBay Standard Envelope shipment,
  and told buyers to examine photographs even when the listing carried none. Packaging is now
  yours to write and omitted when blank; the photograph sentence only appears when photographs
  are attached.
- Titles now reproduce eBay's own catalogue name — `NAME NUMBER SET FINISH` — which is what
  attaches a listing to the product page the price guide lives on. Condition and rarity are out
  of the title; both are item specifics with their own search facets. See D-111.
- **Fixed**: the "Sell one like this" URL used `mode=SellLikeItem`. It is `SellSimilarItem`.
- The eBay tab was removed and the export moved into Inventory, which already knows what each
  card is worth and which lot it is in. See D-116.

### Pricing
- **Real eBay sold prices.** Select cards in Inventory and press *Get real eBay prices*: comps
  come from completed listings via Apify, matched to the card's condition, with graded slabs and
  multi-card lots excluded. With three or more comps in the exact grade the median is used as-is
  — no multiplier, no floor. Never automatic; each result costs money. See D-120.
- **Under $0.50 on TCGplayer NM is bulk**, judged on the unadjusted feed price. Real sold comps
  override it. Reclassified seven of 33 cards. See D-121.

### Correctness
- **`make smoke`** exercises every endpoint against the running stack — 38 checks including the
  rule that an unknown id is always 4xx and never 5xx. The unit suite deliberately skips anything
  needing a database, so this half of the API was previously only tested by using the app. It
  found two shipped bugs on its first run. See D-113.
- **Fixed**: `/api/inventory/{sku}/listing` never emitted `title_length`; the character counter
  had been rendering `undefined/80`.
- **Fixed**: the eBay queue asked for `ImageKind.PROCESSED`, which does not exist. Every request
  500'd.
- **Retracted**: the promo set-name table shipped with fabricated entries. Only the Sword &
  Shield mapping came from a real listing; the rest are now derived from it, and inferred names
  are flagged in the UI for a second look.
- 341 tests, up from 275.

## 2026-09-01

### Inventory and lots
- CSV export of the collection or a single lot, carrying the eBay condition code and label
  alongside the rubric grade. See D-110.
- Lots gained an asking price and card removal; cards can be un-approved from the inventory.
- New **Inventory** tab: every card with its eBay estimate, net, and list/marginal/bulk verdict,
  sorted by value, with collection totals and filters. See D-108.
- **Lots**: select cards and bundle them. eBay's fixed per-order fee and postage are paid once
  per order, so the same twelve cards net $11.23 as one lot against $6.02 sold separately —
  nearly double, for one listing instead of twelve. See D-109.

### Phase 5 — pricing
- **Fixed**: cards that sell on eBay were being written off as bulk because the TCGplayer feed
  quotes them ~10x lower. The listing verdict now judges against an eBay estimate with a $0.99
  floor. Ten cards moved from "bulk" to "marginal". See D-106.
- The sold-listings link now finds the actual card: full printed number in quotes plus the
  finish, excluding lot and "choose your card" listings. See D-107.
- The approve screen has a Price section: the condition-adjusted figure, what lands after eBay's
  fees, a list/marginal/bulk verdict, an inline price box, and a link straight to that card's
  sold listings on eBay. See D-105.
- Each card reports what actually lands after eBay's percentage, the fixed per-order fee and
  postage, plus a verdict: list, marginal, or bulk. Who pays shipping is configurable —
  break-even is $0.55 with buyer-paid postage against $1.85 with free shipping. See D-104.
- Tried and rejected deriving condition discounts from the TCGplayer low/market spread: `low` is
  the cheapest listing, not a played copy, and carries no condition information.
- Prices are quoted in USD only; euro figures are stored but never shown, and nothing converts.
- The headline value is adjusted for the card's condition, with the Near Mint figure, the
  multiplier and eBay's fee shown alongside. See D-103.
- A price can be entered by hand from eBay completed listings and outranks the marketplace
  feeds. eBay sold data needs their access-restricted Marketplace Insights API.
- `make prices` now prices only cards in inventory (25 cards in 6.9s) instead of walking the
  23,544-card catalogue, skips anything priced within 7 days, and paces its requests. `--all`
  remains for a full catalogue run. See D-102.
- **Fixed a mispricing**: every printing's prices were written to every variant of a card, and
  Cardmarket's foil figures were ignored entirely — a reverse holo was priced as a normal
  ($0.08 vs $0.24 on one measured card). Each variant is now priced from its own printing.
- Cards carry a value with its age; a price older than 7 days is marked for refresh.

### Cleanup
- Removed 463 lines of measured-and-rejected imaging code (Coons boundary warp, thin-plate-spline
  warp, foil coverage) along with the `enable_boundary_warp` flag and its branch in the dewarp
  path. The findings live in DECISIONS.md, which is where they are useful. See D-101.
- Removed the orphaned Review component and three unused API client methods.
- The dashboard's points-rubric panel is now a list of what the condition codes mean.

### Condition, simplified
- Condition is now five one-click buttons (NM/LP/MP/HP/DMG), settable and changeable in the
  approve screen or the card editor, and clearable. The defect-chip rubric UI is gone; the
  points rubric and marketplace translations remain behind the API for eBay listing. See D-100.
- Condition no longer blocks approval — identity and variant still do, because those are claims
  the pipeline made and a person must check.
- The twelve Near Mint grades created by the old combined button are cleared.

### Strictness and a full-run shakedown
- The variant picker says when the catalogue has no real printing data (26% of all variants are
  synthesised by TCGdex, and whole promo sets carry none), so a generated "Normal" is no longer
  presented as fact. See D-099.
- A finish the catalogue does not list can be recorded by hand. TCGdex reports XY48 Meowstic as
  normal-only when it exists only as a holo, which made the card impossible to finish. Locally
  added variants are marked so a sync cannot revert them. See D-097.
- **Fixed**: the primary approve action no longer asserts a grade. "Near Mint & confirm" created
  Near Mint assessments that an operator reasonably read as confirming an automated result.
  Confirming a card and stating its condition are now separate acts. See D-095.
- Grades save automatically as imperfections are tapped; no separate save step.
- "Near Mint & confirm" is withdrawn on any capture the pipeline flagged, so the optimistic
  grade is never the easy path on a photograph that cannot show wear. See D-094.
- **Fixed**: OCR misread numbers at some capture resolutions (35/113 as 5/13), which
  misidentified a card. The patch is now normalised to a fixed height. See D-092.
- **Fixed**: correcting a card's identity left the variant picker stale and empty, blocking
  approval until a page reload. See D-093.

### One queue
- The Review tab is gone. Everything needing attention is surfaced in **Approve**, as a banner
  above the card. Approve gains "Set aside" and "Needs a rescan" so a hard card cannot stall the
  queue, both with counts and one-click restore in the header. See D-091.
- Adjusting corners now re-renders the listing image, which previously kept the old crop and made
  the correction look like it had done nothing. See D-090.

### Approval gate
- **Fixed**: cards could be approved without being graded, so Approved climbed while Condition
  graded stayed flat and the card could never be listed. Approval now requires identity, variant
  and grade, and the button says what is missing. "Near Mint & confirm" keeps the common case to
  one press. See D-089.
- Everything on the approve screen is editable in place — identity search, variant, grading and
  corner adjustment — with no button to reveal an editor. See D-088.
- New **Approve** tab: every card is confirmed by a person before it counts as done. One card at
  a time with listing images large, processed small, identification, variant and grade, plus
  magnified patches of both bottom corners for checking the collector number and set symbol
  against the named card. Enter confirms. See D-085.
- **Fixed**: listing backs could go missing permanently while the progress bar read "done".
  Renders now rebuild after either side processes, `make reconcile` detects and repairs the
  state, and the progress stage requires both images. See D-086.
- The interface uses the full screen, and strips wrap instead of scrolling sideways. See D-087.

### Phase 4 — condition grading
- Every card always shows all four renders; a missing one becomes a placeholder saying whether
  it is processing, failed, or never captured. See D-082.
- Condition assessments persist against a card: `POST /conditioning/{sku}/assess` records what
  the operator saw, the rubric decides the grade, and the item is marked graded. Append-only, so
  a Phase 11 model prediction and the human correction after it both survive. See D-080.
- Grading UI in the card editor: one button for Near Mint, and eleven imperfection chips that
  cycle through severities with a live grade and its arithmetic shown.
- Variant labels now carry stamp and subtype, so two "Normal" printings that differ only by a
  set-logo stamp are distinguishable. See D-081.

## 2026-08-31

### Search, rescan
- Card search parses "charmander 49" and "charmander secret wonders" — name plus collector
  number or set, in one box. See D-077.
- Image-quality flags gained "Photo is poor — needs a rescan", collecting cards into a Rescan
  list that clears itself when a new capture arrives (migration 0010). See D-078.

### Cropping regression fixed, and a progress panel
- **Fixed**: the half-decode broke cropping, leaving stored corners in half-resolution space so
  captures cropped to the top-left quadrant. Detection now runs on the small image, corners are
  scaled back to the original's coordinates, and the warp samples the full-resolution original —
  so cropping is correct and listing images keep every pixel. See D-075.
- New Progress panel on the dashboard: backlog per pipeline stage, derived from the data rather
  than from the review queue, plus what is waiting on a person. See D-076.

### Pi efficiency and the card editor
- Captures decode at half resolution: 2.5x faster, a quarter of the memory, and detection
  confidence rises (0.925 -> 0.946). Processed sides drop from ~1.5 MB to ~700 KB. See D-073.
- `recrop_front` no longer decodes separately, which had fronts at 32.8 px/mm and backs at 18.3.
- New card editor, opened by clicking a card in Review or Capture: images, catalogue search to
  correct the identity, variant picker, both corner editors, reprocess and re-identify. See
  D-074.
- Corner editor gained edge guides that extend past each corner, for sighting along an edge the
  way a ruler is lined up.

### Pi readiness and reliability
- OCR is skipped when registration is already decisive: it was 3.80 s of a 4.49 s
  identification. Average now 5.59 s per card, best case 1.95 s. See D-071.
- A failed re-identification no longer overwrites the confidence of one that succeeded. See
  D-072.
- Reference art is cached on disk and survives rebuilds; `make warm-art` pre-downloads a set so
  recognition never waits on the network. See D-069.
- Recognition now fails fast when the art source is down: 93 s -> 30 s, via a circuit breaker, a
  6 s fetch timeout and a time budget checked before every fallback stage. See D-069.
- Variants are never auto-assigned, not even when the catalogue lists one. See D-067.
- "Skip" became "Later": a deferred review stays open and can be brought back (migration 0009).
  See D-068.
- Scan tab locks scrolling, over-scroll and pinch-zoom, and offers full screen so the tab cannot
  be closed by a mis-tap.
- Rotation and flip handling measured: 18/18 upright after rectification, 8/8 identified after
  four-orientation hash recovery. See D-070.

### Scanning and review UX
- The variant can be changed from the capture strip: open a card and pick. Answering there also
  closes any open variant review, so the same question is never asked twice.
- `/review/{id}/resolve` now checks a chosen variant actually belongs to the card. Without it a
  stale id silently attached another card's variant — invisible in the UI, wrong for pricing and
  listing, which both key off `card_variant_id`.
- New **Scan** tab: a mobile-first screen with one large shutter, side prompt, session counter,
  last card scanned, haptic feedback and a 700 ms debounce. See D-065.
- Recent captures now show listing front, listing back and processed side by side, with the
  variant in each card's title.
- Fixed the review queue reordering under the operator, which made a card's remaining reviews
  unreachable after answering one. See D-066.
- Image reviews gained a "Re-run the crop" action; previously the only option was to disagree
  with the flag.
- Dashboard copy corrected (it still announced Phase 2) and each tab's purpose stated.

### Phase 3 — recognition
- Collector-number OCR (TASK-019) and signal fusion (TASK-020). Reads a usable number on 20/20
  real captures; the set total re-ranks candidates the artwork cannot separate. See D-062.
- A confident number now also acts as a catalogue lookup, recovering cards the perceptual hash
  never shortlisted: CARD-000014 went from Zebstrika at 0.217 to Empoleon at 1.000. See D-063.
- Recognition runs automatically once a front is processed; no button. See D-064.
- New Review tab: one queue of cards needing a decision, with recent clean cards shown quietly
  beneath. Variant and identification choices apply in one click. See D-064.
- Fixed the blur gate, which was failing all 20 captures — including, when measured the same
  way, TCGdex's own reference art. Now resolution-normalised. See D-061.

### Straightening
- Cancelled a systematic +0.64 deg tilt in the card-back reference (`REFERENCE_ROTATION_DEG`).
  Backs measured +0.64 deg median across all twelve cards while fronts measured +0.00; after
  the fix backs are +0.00 median, sd 0.133, worst 0.401 deg — better than fronts. See D-059.
- `/capture/{sku}/reprocess` now chains recognition for already-identified cards. A bare
  reprocess rebuilt corners from edge detection alone and regressed fronts from sd 0.22 to
  1.59 deg. See D-060.

## Phase 1 — Foundation

- Docker Compose stack: web, api, worker, postgres, redis; optional self-hosted tcgdex profile.
- Postgres schema, 18 tables, one initial migration. `user_id` on every owned row.
- TCGdex sync: sets, cards, variants including `variants_detailed` variant IDs. Incremental and resumable.
- Price ingest from TCGdex's Cardmarket and TCGplayer data into an append-only `prices` table.
- Storage layer with `CARD-NNNNNN/{original,processed}-{front,back}.jpg`, local backend, S3 interface.
- Condition rubric seeded as reference data; marketplace condition translations seeded for
  eBay, TCGplayer, Cardmarket and Collectr.
- Health endpoints, hardware detection script, Makefile, test suite.

### Fixed during first-run verification

- `card_variants` uniqueness now includes `stamp_key`. Without it Base Set Charizard's shadowless
  and shadowless-1st-edition printings collapsed into one row.
- `tcgdex_variant_id` is no longer unique. It names a variant *class* shared by many cards, so a
  unique index both blocked inserts and would have attached one card's prices to another.
- Duplicate entries within a single card's `variants_detailed` are collapsed.
- Alembic runs on asyncpg instead of pulling psycopg2 in as a second driver.
- Postgres enums store member values (`market`) rather than names (`MARKET`).
- `api` and `worker` share one Compose image; they were building separately, so rebuilding the
  API left the worker running stale code.
- `--refresh` rewrites rows even when the upstream payload is unchanged.
- The engine is created lazily, so importing a model no longer requires a reachable database.
- Unknown imperfection names return 422 instead of 500.
- Collectr condition mapping uses full words (`Near Mint`), confirmed against a real export.
- Variant labels render "1st Edition", not "1St Edition".
- nginx listens on IPv6 and healthchecks use 127.0.0.1; `localhost` resolved to ::1 inside the
  container, so the web healthcheck failed while the site served fine.
- `make seed --reset` re-applies shipped defaults over user-editable translation rows.

## Phase 2 — Image pipeline

- Capture API with automatic front/back pairing: a front starts a card, a back finishes the one
  waiting. No filenames, no manual linking. Batch upload pairs by filename hint or arrival order.
- Card detection and perspective dewarp to exactly 88 x 63 mm at 20 px/mm (1260 x 1760), so a
  measured pixel area converts to mm² by division.
- Quality gates at ingest: blur, exposure, effective resolution and specular glare, each with the
  measurement kept alongside the verdict.
- Processing runs on the queue; capture returns in ~50 ms regardless of pipeline time.
- Originals are written once and never modified. A processing failure preserves the original and
  opens an image review with an actionable reason.
- Capture UI: live camera preview, space-bar shutter, side prompt, recent-captures strip with
  per-card processing state.
- Migrations 0002 (image metadata) and 0003 (one open review per item per category).

### Fixed during Phase 2 verification

- `dewarp` now orders its own corners; an unordered quad warped to silently wrong geometry.
- `/{sku}/reprocess` was being swallowed by the `/{sku}/{side}` route and demanding a file upload.
- Reviews raced: both sides of a card reconciled concurrently and each opened one. Serialised with
  a row lock plus a partial unique index.
- "Not processed yet" was treated as a defect, so the faster side of a card opened a review about
  the slower one.
- A successful reprocess now resolves the review it once opened, instead of leaving a stale reason.
- `make revision` bind-mounts the versions directory; generated migrations were vanishing with the
  container.
- `make test` and `make lint` bind-mount the source, so editing a test no longer needs a rebuild.
- Migration 0002 creates its enum types explicitly and drops them on downgrade; Alembic's
  `add_column` does neither, so the generated version failed against a real database.
- Dashboard was rendering underneath the capture view: `hidden` does not beat `display: grid`.

### Fixed after the first real photographs

- **Detection found nothing on real photos.** Canny alone does not survive a card held against a
  face and a textured wall. Four edge strategies now compete in one candidate pool, contours are
  approximated via their convex hull as well as directly, epsilons run fine to coarse, and quads
  contained inside a larger plausible quad are suppressed.
- **Sub-pixel corner refinement.** Thresholding biased corners outward by ~6 px, which is 0.3 mm
  of phantom card on the edgewear measurement. Synthetic corner error: 11 px → 1.0-1.6 px.
- **The framing guide was lying.** A 4:3 preview with `object-fit: cover` cropped the 9:16 phone
  stream, so a card lined up against the guide landed at 16% of the actual frame. The preview now
  uses the stream's true aspect ratio.
- Capture now uses the sensor's maximum resolution, and the UI shows it alongside the px/mm the
  capture would achieve if the card filled the guide.
- Added a camera picker, and a `capture="environment"` file input so a phone can shoot at full
  resolution over plain HTTP, where `getUserMedia` would need HTTPS.
- **"Processing…" was shown for captures that had already failed.** Now says "not detected" with
  the reason.
- **Set corners**: a failed detection can be rectified by hand from four draggable handles.
- `GET /capture/pending` and `/capture/recent` were shadowed by `/capture/{sku}` — the second time
  this class of bug appeared, so there is now a test asserting route declaration order.

### Image quality

- Rectification scale now follows the capture (10-80 px/mm) instead of a fixed 20. Poor captures
  are no longer upscaled into bigger files with no more information, and a good phone camera's
  detail is no longer thrown away.
- JPEG quality 97 with 4:4:4 chroma; capture-side quality raised to 0.98 and the upload limit to
  60MB for 48MP phone photos.
- `mm_from_px` / `mm2_from_px2` / `px_from_mm` centralise the conversion, so changing the scale
  cannot desynchronise measurement from the millimetre thresholds.
- Corner handles are hollow rings with crosshairs, a magnifier that follows the selected corner
  and sits opposite it, a live coordinate readout, and arrow-key nudging.

### Cropping accuracy

- Corners now come from RANSAC-fitted edge lines intersected pairwise, computed at full
  resolution. Synthetic corner error 11px → 1.0-1.6px → **0.51-0.73px**.
- Fitting skips the rounded ends of each edge, so it recovers the corner of the card's virtual
  sharp rectangle rather than the rounded corner the contour actually has.
- Manual corners are snapped to the fitted edge when the move is under 14px.
- A card touching the frame edge is flagged as cropped rather than silently mis-cut.
- The Coons boundary warp was implemented, measured, found worse than the homography on both a
  real photo and synthetic barrel distortion, and left disabled behind `ENABLE_BOUNDARY_WARP`.
- Image URLs carry a content hash, so a re-rectified card no longer shows its old crop from the
  browser cache.
- `detection_method` is persisted; the capture strip marks hand-corrected sides.
- The corner adjuster handles front and back in one visit and moves to the other side on save.

### Same capture on phone and laptop

- The web service can serve HTTPS from a self-signed certificate (`make cert`) covering
  localhost and every LAN address, so a phone gets the same in-page camera the laptop has.
  Optional: without a certificate nginx serves plain HTTP unchanged.
- The UI detects a non-secure context specifically and prints the exact HTTPS URL to open,
  rather than reporting a camera failure.
- The native camera picker is demoted to a labelled fallback, shown only when the in-page
  camera is unavailable.
- iOS autoplay: a "tap to start the camera" button appears if Safari refuses inline playback.
- nginx upload limit raised to 64MB for 48MP phone photos.

### Detection picking the background

- Suppression now keeps the best-scoring quad rather than the biggest. The previous rule
  discarded a perfect detection of a card (confidence 1.000) in favour of the mat it was lying
  on, whose aspect matches a sideways card's.
- Landscape quads carry a modest penalty, since a card's artwork window is always landscape and
  a card usually is not; quads hugging the frame border are penalised as likely background.
- "Set corners" is available on every card regardless of score, red only when attention looks
  warranted.
- Tests assert that the crop keeps the card border and excludes background — edgewear lives in
  the outermost millimetre, so both directions matter.

### Learning from the real captures

- `make benchmark` runs detection over every stored original and scores it. Every scoring change
  below came from that loop rather than from reasoning about it.
- Card size is scored as a band (0.30-0.66 of frame) instead of monotonically, so neither a
  graphic inside the card nor the mat around it scores respectably any more.
- `make mark ARGS="CARD-000005 --no-card"` labels deliberate negatives (migration 0005), so a
  correct rejection is not counted as a failure.
- Landscape penalty settled at 0.82; 0.70 was measured and reverted for breaking sideways cards.
- All ten cards reprocessed front and back; CARD-000009 deleted at the user's request.

### Card backs

- Candidates are ranked by **measured edge support** — all four sides line-fitted at full
  resolution — rather than filtered by a shape gate that was discarding the correct answer.
  On the second real batch this took the benchmark from 7/20 to 19/20.
- The shape gate now penalises rather than discards: it asks how well a quad is filled by its
  contour, which is the wrong question for a low-contrast border traced only in part.
- Added a CLAHE edge strategy for dark-card-on-dark-mat boundaries.
- Nested-frame replacement now requires the larger quad to score comparably, so "prefer the
  outer frame" cannot silently become "prefer the bigger thing" again.
- nginx re-resolves the API upstream; recreating the api container used to leave the UI on 502s.

### Card backs solved by registration

- Card backs are now located by ORB registration against a reference back rather than by finding
  edges: 10/10 backs correct on the real benchmark, 0/10 false positives on fronts, ~30ms each.
  Fixes the pokéball crops, the 90° rotations and the skewed quads in one change.
- Reference expansion of 1.04 measured two independent ways, not guessed.
- `make sheet` renders every processed crop as one contact sheet. Numbers said a back cropped
  inside its own border was 0.99 confident; twenty thumbnails settled it in seconds.
- ORB feature count capped at 1200: brute-force matching is quadratic and 3000 cost 78s of test
  time for no extra accuracy.

### Reliability

- Lost processing jobs are now detectable: `make reconcile` finds captures with no processed
  counterpart and no error, and re-queues them. Found on a clean re-ingest where 1 of 20 jobs
  silently never ran. The count is surfaced on `/capture/pending`.

## Phase 3 — Card recognition (in progress)

- Perceptual hash index over the catalogue, stored in Postgres rather than mirroring ~2GB of
  art (migration 0006). `make index-art` is resumable and records per-card failures.
- Hamming search runs in the database via `bit_count` on the XOR.
- `imaging/register.py`: Phase 2's card-back registration generalised for any reference, giving
  identification confidence, an exact homography for re-cropping, and recovered orientation.
- `make migrate` bind-mounts the versions directory. Migrations are baked into the image at
  build time, so a migration written since the last build was invisible to alembic and
  `upgrade head` silently reported it was already up to date.

### Recognition, persisted and visible

- `POST /capture/{sku}/recognize` identifies a card, records it, and re-cuts the crop from the
  recognised artwork in the same request. ~2s per card.
- Identifications persist to `inventory_items` (card, confidence, status) and appear in the
  capture strip; unidentified cards get an Identify button.
- A non-decisive result opens an identification review with ranked candidates rather than
  writing a guess.
- Re-cropping from artwork: 179-280 inliers on all ten real captures. The trainer card that was
  cropped to its artwork panel and rotated 90 degrees went from border score 0.084 to 0.84.
- 10/10 identified on the real batch, all at confidence 1.00, none needing review.

### Listing images

- `listing_front` / `listing_back`: presentation copies with a consistent 5mm margin of real
  background on every side, so a buyer can see the card's edges and corners (migration 0007).
  Rendered from the original using the processed corners; the processed image is untouched.
- Card-back reference rebuilt by consensus: `make back-reference` median-blends every stored
  back after snapping each to its true card edge. Optimal expansion now measures 1.00, down
  from a bootstrapped 1.04 that was chasing its own error.

### Consistency

- A decisive registration (>=60 inliers) now always wins the crop. Border evidence had been
  vetoing correct art crops on cards with low card-to-mat contrast — full-art cards and dark
  backs — which made two captures of the same card produce different-looking images.
- All twelve cards now carry `artref` crops.
- `/health/detail` reports `enum_drift`: a running container whose enums predate the schema
  fails on read and takes out whole endpoints, which is how `/capture/recent` began 500ing.

### Cropping, confirmed against references

- Card backs were being cropped to the inner artwork panel, losing the blue border where edge
  wear is measured. Registration locks onto the panel because the border has no features.
  Offset calibrated visually at 1.12 after three automated attempts failed on the same
  low-contrast blue-on-black problem.
- TCGdex art confirmed against line-fitted card edges on ten real captures: accurate to 0.45%.
  A 1.2% outset guards the homography on holo cards, not the art.
- The shutter now waits only for the frame grab; uploads happen in the background and the side
  prompt flips immediately. Outstanding uploads are shown in the session panel.

### Rotation

- Orientation is now recovered from the reference's corner order, so a card photographed
  upside-down or sideways rectifies upright. Verified on a real photo at 0/90/180/270 degrees.
- `dewarp(oriented=True)` honours caller-supplied corner order and skips the landscape check,
  which would otherwise rotate a second time.
- Edge fitting remains the documented backup where no reference matches: it gives correct
  geometry (square, portrait, cropped) but not orientation, which needs content.
- Listing margin unchanged and verified: 5mm on every side.

### Uniform listing output

- Listing images render at a fixed scale (`LISTING_PX_PER_MM`, default 30), so every one is the
  same pixel size regardless of capture distance. All 24 now render at 2190x2940.
- Spacing is adjustable across the whole collection: `make listings ARGS="--margin 9"`.
- Clean run of all 12 cards from their originals: 12/12 fronts `artref`, 12/12 backs `backref`,
  12/12 identified at confidence 1.00.

### Full-art cards

- The crop safety outset now scales with registration confidence (1.012 at 200+ inliers to
  1.035 at the floor), because foil halves the inlier count and a fixed outset let full-arts
  overflow the listing margin.
- Improves but does not close the gap: a homography from 76 correspondences is less precise
  than one from 280. Documented as needing better evidence, not more tuning.

### Variants

- Cards printed in a single variant are assigned it automatically; cards printed in several open
  a variant review listing the options. 2 of 12 resolved automatically.
- Foil-based classification was built, measured and rejected: the same card photographed twice
  gave foil coverage of 0.094 and 0.164, because foil only shows at particular light angles.
  Needs the controlled lighting of the capture rig, not a better threshold.

### Non-planar cards and lens distortion

- Registration now keeps its inlier correspondences rather than discarding them into a
  homography, so a non-rigid warp can be attempted from real evidence.
- A thin-plate spline through those correspondences was built, measured and rejected: worse
  than the homography on all 12 real captures, by 0.02 to 0.30, and unchanged by sweeping
  regularisation across five orders of magnitude. ORB inliers stop ~7% short of every card
  edge, so the spline extrapolates into precisely the region that decides the crop.
- Kept as `imaging/elastic.py`, disabled, documenting why so it is not attempted again.

### Prefilter

- Recognition prefilter is now a 4x4 spatial grid hash (1024 bits) rather than a single 64-bit
  perceptual hash (migration 0008). Rank-1 recall over 21,775 cards went from 7/12 to 12/12,
  and both cards previously outside the top 20 now come first.
- Searched in memory with numpy: ~42ms over the whole catalogue, ~2.8MB resident.
- Verification shortlist cut from 20 candidates to 8 as a result: ~7.9s to ~1.1s per card,
  still 12/12.
- `index-art` now commits per batch, so an interruption during a ten-minute run no longer
  discards everything.
