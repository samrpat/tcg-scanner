# Collectr

Reference only. The adapter itself is **Phase 6** and is not built.

## How data gets in

Collectr has no import API. You email them a CSV and they attach it to a portfolio of your
choosing. So the deliverable is a file that matches their own export format exactly, and the
`Portfolio Name` column is what tells them where it goes.

Getting an export out: **Settings → Export → CSV** (PRO only).

## Format

Confirmed against a real export, 2026-08-23. UTF-8, **no BOM**, LF line endings, no quoting
observed (a name containing a comma presumably quotes normally — assume RFC 4180 on write).

| # | Column | Example | Notes |
|---|---|---|---|
| 1 | `Portfolio Name` | `Sameer` | Destination portfolio. Required for the email workflow. |
| 2 | `Category` | `Pokemon` | Game. |
| 3 | `Set` | `Paradox Rift` | **Display name**, not a code. Needs mapping from TCGdex set names. |
| 4 | `Product Name` | `Toxtricity ex` | Card name. |
| 5 | `Card Number` | `227/182`, `027` | Format varies: `n/total` for set cards, bare `n` for promos. |
| 6 | `Rarity` | `Ultra Rare`, `Promo` | Display name. |
| 7 | `Variance` | `Holofoil` | TCGplayer-style printing: `Normal`, `Holofoil`, `Reverse Holofoil`. |
| 8 | `Grade` | `Ungraded`, `PSA 8.0 NM - MT` | Graded slabs share this column with raw cards. |
| 9 | `Card Condition` | `Near Mint` | **Full words, not codes.** Emitting `NM` would fail. |
| 10 | `Average Cost Paid` | `3.0000` | Four decimal places. |
| 11 | `Quantity` | `1` | Integer. |
| 12 | `Market Price (As of YYYY-MM-DD)` | `3.7` | **The header contains a date.** Match this column by prefix, never by exact string. |
| 13 | `Price Override` | `0` | `0` when unset. |
| 14 | `Watchlist` | `false` | Lowercase string, not a boolean. |
| 15 | `Date Added` | `2026-08-03` | ISO date. |
| 16 | `Notes` | | Free text, usually empty. |

## Consequences for our schema

- **Condition** maps to full words. Corrected in `condition_translations` (see D-004).
- **`Set` and `Rarity` are display names**, so Phase 6 needs a TCGdex-name → Collectr-name
  mapping table, with a review queue entry when a set name has no match rather than a silent
  guess. Promo sets are the likely troublemakers (`Scarlet & Violet Promo`).
- **`Variance` is TCGplayer vocabulary**, not TCGdex's. Our `card_variants.type` maps
  `normal → Normal`, `holo → Holofoil`, `reverse → Reverse Holofoil`. Subtype and stamp
  (shadowless, 1st edition) have **nowhere to go** in this format — a real fidelity loss on
  vintage, and worth flagging to the user before Phase 6 rather than discovering at import.
- **`Card Number` needs the set total** for non-promos, which we have as
  `card_sets.card_count_official`.
- **`Grade`** is `Ungraded` for everything we produce; the platform grades raw cards only.
