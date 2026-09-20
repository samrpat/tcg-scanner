# Third-party licenses

Reviewed for Phase 1. Every entry lists commercial implications even though the project is currently
personal (D-011), so the position is known if that ever changes.

## Runtime dependencies

| Name | Version | License | Purpose | Commercial implications |
|---|---|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | 0.115.x | MIT | HTTP API | None |
| [Uvicorn](https://github.com/encode/uvicorn) | 0.32.x | BSD-3-Clause | ASGI server | None |
| [SQLAlchemy](https://www.sqlalchemy.org/) | 2.0.x | MIT | ORM | None |
| [asyncpg](https://github.com/MagicStack/asyncpg) | 0.30.x | Apache-2.0 | Postgres driver | None |
| [Alembic](https://alembic.sqlalchemy.org/) | 1.14.x | MIT | Migrations | None |
| [Pydantic](https://docs.pydantic.dev/) | 2.x | MIT | Validation, settings | None |
| [httpx](https://www.python-httpx.org/) | 0.28.x | BSD-3-Clause | TCGdex client | None |
| [arq](https://github.com/python-arq/arq) | 0.26.x | MIT | Job queue | None |
| [redis-py](https://github.com/redis/redis-py) | 5.x | MIT | Queue transport | None |
| [Pillow](https://python-pillow.org/) | 11.x | MIT-CMU | Image handling | None |
| [structlog](https://www.structlog.org/) | 24.x | Apache-2.0 / MIT | Logging | None |
| [tenacity](https://github.com/jd/tenacity) | 9.x | Apache-2.0 | Retries on TCGdex | None |
| [PostgreSQL](https://www.postgresql.org/) | 16 | PostgreSQL License | Database | None, permissive |
| [Redis](https://redis.io/) | 7.4 | RSALv2 / SSPLv1 | Queue | Source-available since 7.4. Fine for self-hosted use; a hosted commercial offering would need [Valkey](https://valkey.io/) (BSD-3) instead. Drop-in replacement. |
| [nginx](https://nginx.org/) | 1.27 | BSD-2-Clause | Static serving, proxy | None |
| [React](https://react.dev/) | 18.x | MIT | Frontend | None |
| [Vite](https://vitejs.dev/) | 6.x | MIT | Frontend build | None |
| [Ubuntu font](https://design.ubuntu.com/font) | v21 / v19 mono | [Ubuntu Font Licence 1.0](https://ubuntu.com/legal/font-licence) | The interface typeface | None. The UFL permits redistribution of the unmodified fonts, including embedding in a commercial product. The files in `web/src/fonts/` are Google Fonts' unmodified latin-subset woff2 builds; they are **not** modified, which is the condition that would otherwise require renaming. |

## Data sources

| Name | License / terms | Purpose | Implications |
|---|---|---|---|
| [TCGdex](https://github.com/tcgdex/cards-database) | MIT (code and data) | Cards, sets, variants, prices | None. Self-hostable, so no dependency on their uptime. |
| [pokemon-tcg-data](https://github.com/PokemonTCG/pokemon-tcg-data) | MIT | Secondary reference, ID crosswalk | None. Not yet used. |
| Cardmarket / TCGplayer prices via TCGdex | Redistributed by TCGdex | Pricing | Prices are facts and stored with source and timestamp. Not resold. |
| [Collectr](https://getcollectr.com/api-terms-and-conditions.html) | Proprietary, API not used | Export target only | Avoided deliberately, see D-001. We never call their API. |

## Deferred, reviewed in advance

| Name | License | Phase | Implications |
|---|---|---|---|
| [CollectorVision](https://github.com/HanClinto/CollectorVision) | **AGPL-3.0** | 3 | Copyleft reaches network use. Acceptable while the project is personal (D-011). Runs as a separate process behind `CardRecognitionEngine`, never linked in, so it can be swapped out. A commercial license is sold separately if that ever becomes necessary. |
| [the_tin](https://github.com/the-tin-app/the_tin) | **AGPL-3.0** | 3 | iOS app, not directly reusable. Reference only for its ORB + codebook fingerprinting approach. |
| [OpenCV](https://opencv.org/) | Apache-2.0 | 2 | None |
| [ONNX Runtime](https://onnxruntime.ai/) | MIT | 3 | None |
| [hnswlib](https://github.com/nmslib/hnswlib) | Apache-2.0 | 3 | None |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | Apache-2.0 | 3 | None, for collector-number OCR |

## Bundled assets

`api/app/imaging/assets/pokemon-back.jpg` is a rectified crop of a photograph of a Pokémon card
back, taken by the user, used as a registration reference. It is card artwork owned by Nintendo /
Creatures / GAME FREAK. Fine for personal use; a commercial release would need it replaced with a
generated or licensed reference, or with a descriptor file that stores only ORB features and not
the image itself.

Pokémon and the Pokémon TCG are trademarks of Nintendo, Creatures Inc. and GAME FREAK inc.
This project is unaffiliated with and unendorsed by any of them.
