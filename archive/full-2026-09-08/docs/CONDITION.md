# Condition

Source: [TCGplayer Card Conditioning Standards, March 2025](https://mktg-assets.tcgplayer.com/web/seller/guides/Card-Conditioning-Standards.pdf).
This is the rubric the whole system grades against (D-003). It is arithmetic, not opinion.

## The system

Observe imperfections → assign each a severity → sum points → the total picks the bucket.

```
Slight = 1    Minor = 2    Moderate = 4    Major = 8   points per instance

NM ≤ 3        LP ≤ 6       MP ≤ 12         HP ≤ 24     DMG > 24
```

## Card geometry

Pokémon and Magic cards: **88 × 63 mm**, area **5,544 mm²**, total border length **302 mm**.
Every threshold below is expressed against those numbers. Because Phase 2 dewarps each card to that
exact rectangle, pixels convert to millimetres deterministically — which is what makes automated
measurement possible without a trained model (D-007).

## Thresholds by imperfection

Each cell is the maximum allowed at that condition.

| Imperfection | NM | LP | MP | HP | DMG |
|---|---|---|---|---|---|
| **Edgewear** (length) | Slight, 20 mm (6.6%) | Minor, 80 mm (26.5%) | Minor, 160 mm (53%) | Moderate, >160 mm | any |
| **Surface Wear** (area) | none | Slight, 2.5 mm² | Minor, 10 mm² | Moderate, 40 mm² | Major, >40 mm² |
| **Scratch** (length) | Minor, 40 mm (45.5%) | Minor, 40 mm | Moderate, >40 mm | any | any |
| **Scuffing** (area) | Slight, 315 mm² (6%) | Minor, 2,772 mm² (50%) | Moderate, 5,544 mm² (100%) | Major, 11,088 mm² (both faces) | any |
| **Indentation** (area) | Slight, 1 pinpoint | Minor, 4 mm² | Moderate, 25 mm² | Major, >25 mm² | any |
| **Bend** (length) | none | Minor, 10 mm (11.4%) | Moderate, 20 mm (22.7%) | Major, >20 mm | any |
| **Grime** (area) | none | Slight, 2.5 mm² | Moderate, 277.5 mm² | any | any |
| **Fault** (area) | none | none | Slight, 2.5 mm² | Minor, 10 mm² | Moderate, 40 mm² |
| **Defect** (area) | Slight, 2.5 mm² | Minor, 5 mm² | Minor, 5 mm² | Moderate, 10 mm² | Major, >10 mm² |
| **Curling** (lift) | 5 mm | 5 mm | 5 mm | 5 mm | >5 mm |
| **Damage** | none | none | none | none | any |

Indentation at NM/LP must not show through to the other side. Scuffing at MP means total coverage
with no more than a 5 mm² clear patch; if coverage is partial, score it as two instances of Minor.

## Two exclusions that matter

**Centering is not a condition factor** up to 70/30. Beyond that, list with a photo under the
condition the card would otherwise earn. This is the single biggest reason PSA grades cannot drive
raw condition — PSA weights centering heavily, and vintage Pokémon is frequently off-center.

**Curl above 5 mm makes a card Damaged** regardless of everything else. A flat photo cannot measure
curl; a raking-light or side shot in the future capture rig could. Until then it is a manual checkbox.

## Not-allowed lists

Certain imperfection types are disqualifying above a condition regardless of points:

- NM forbids Surface Wear, Grime, Bend, Fault, Damage
- LP forbids Fault, Damage
- MP forbids Damage
- HP forbids Damage

## Marketplace translation

Stored canonical, translated only when a listing is generated (D-004). Lives in
`condition_translations`, seeded and editable.

| Canonical | eBay ungraded | TCGplayer | Cardmarket | Collectr |
|---|---|---|---|---|
| NM | `400010` Near mint or better | Near Mint | NM | NM |
| LP | `400011` Excellent | Lightly Played | EX | LP |
| MP | `400012` Very good | Moderately Played | GD | MP |
| HP | `400013` Poor | Heavily Played | PL | HP |
| DMG | `400013` Poor + required photos | Damaged | PO | DMG |

eBay requires condition ID `4000` (Ungraded) plus a `conditionDescriptors` entry with Card Condition
ID `40001` in categories 183050, 183454 and 261328.

## Roadmap

- **Phase 4** — manual mode. The operator ticks imperfections and severities against this rubric;
  the condition computes itself. Produces structured defect labels, not a bare bucket.
- **Phase 11** — automated. Classical CV measures edgewear, whitening, scuffing and indentation
  against these thresholds; an off-the-shelf pretrained detector proposes candidate defect regions.
  No user-trained model required. Phase 4's manual picks become the validation set.

Conservative resolution throughout: when a measurement straddles a boundary, take the worse bucket.
