# Database

PostgreSQL 16. 21 tables. Every row a user can own carries `user_id` from the start (spec §40),
even though the system is single-user today.

## Reference data — synced, not owned

| Table | Notes |
|---|---|
| `card_sets` | TCGdex sets. `tcgdex_id` unique. |
| `cards` | TCGdex cards. `tcgdex_id` unique, full payload kept in `raw` jsonb. |
| `card_variants` | One row per entry in TCGdex `variants_detailed`: type/subtype/size/stamp (holo, reverse, shadowless, 1st-edition, promo). Identity is `(card_id, type, subtype, size, stamp_key)`. `tcgdex_variant_id` is stored but **not unique** — see below. **This is what inventory points at, not `cards`.** |
| `pricing_sources` | Seeded: `tcgdex_tcgplayer`, `tcgdex_cardmarket`, `manual`. |
| `prices` | Append-only. `(card_variant_id, pricing_source_id, price_type, currency, amount, captured_at, source_updated_at)`. Never updated in place — price history is the point. |
| `condition_rubric` | The TCGplayer thresholds from `docs/CONDITION.md` as queryable rows. |
| `condition_translations` | Canonical condition → marketplace code. Seeded for eBay, TCGplayer, Cardmarket, Collectr. Editable in settings. |
| `ebay_sales` | Individual completed eBay listings, per variant and per condition. Kept as evidence rather than folded into an average — a median can be recomputed, and the comps are what makes a price judgeable. `condition` is nullable because plenty of listings do not state one. See D-120. |

## Owned data

| Table | Notes |
|---|---|
| `users` | Single row today. |
| `inventory_items` | The core entity. `sku` (`CARD-000001`) unique per user. References `card_variants`. Deleting a marketplace connection cannot touch this table. |
| `images` | Four kinds per item: `original_front`, `original_back`, `processed_front`, `processed_back`. Stores sha256 and dimensions. |
| `condition_assessments` | **Append-only.** `source` is `ai`, `human` or `import`. An AI prediction is never erased when a human overrides it — both rows survive, one is flagged `is_accepted` (spec §16). |
| `listings` / `listing_items` | Individual or lot. A lot is one listing with many items. |
| `marketplace_accounts` | Credentials are never stored here — only `credentials_ref`, a key into the environment or secret store. |
| `marketplace_listings` | The external side: `external_id`, `url`, `status`, request payload. |
| `external_mappings` | Generic `(entity_type, entity_id, provider, external_id)`. One table for Collectr, eBay and TCGplayer mappings instead of provider-specific columns, so adding a provider is a row, not a migration. |
| `jobs` | Queue mirror for observability. arq holds the live queue; this is the audit trail. |
| `reviews` | Exception queue. `category` is identification, variant, condition, image, pricing, collectr or marketplace. |

## Rules

- **Append-only tables:** `prices`, `condition_assessments`. Never `UPDATE`, never `DELETE`.
- **No external ID is a primary key.** `tcgdex_id` is a unique column on a row whose primary key is
  our own UUID. If TCGdex renumbers, we remap; inventory is unaffected.
- **`tcgdex_variant_id` is not unique and must never be matched on alone.** It names a variant
  *class*, not a row: all 16 holo rares in Base Set share the same four variantIds. A lookup keyed
  on it alone would attach one card's prices to another. Always scope it by `card_id`, or match on
  the shape key `(type, subtype, size, stamp_key)`.
- **`stamp_key` is part of variant identity.** Base Set Charizard has two
  `("holo", "shadowless", "standard")` variants differing only by the 1st-edition stamp.
- **Enums** are Postgres native types: `condition_enum`, `image_kind`, `job_status`,
  `assessment_source`, `listing_kind`, `review_category`, `review_status`.

## Migrations

Alembic, one migration per change, never edited after being applied. `make migrate` applies them.
