#!/usr/bin/env bash
# Database + images backup. Restore on any compatible host (spec §39).
set -euo pipefail

# Read .env so a customised POSTGRES_USER/DB is honoured rather than silently defaulted.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="data/backups/${STAMP}"
mkdir -p "$OUT"

echo "Backing up to ${OUT}"

docker compose exec -T postgres pg_dump \
  -U "${POSTGRES_USER:-tcg}" \
  -d "${POSTGRES_DB:-tcg}" \
  --clean --if-exists \
  | gzip > "${OUT}/database.sql.gz"
echo "  database  $(du -h "${OUT}/database.sql.gz" | cut -f1)"

if [[ -d data/images ]] && [[ -n "$(find data/images -type f ! -name '.gitkeep' -print -quit)" ]]; then
  tar -czf "${OUT}/images.tar.gz" -C data images
  echo "  images    $(du -h "${OUT}/images.tar.gz" | cut -f1)"
else
  echo "  images    none yet"
fi

[[ -f .env ]] && cp .env "${OUT}/env.backup" && chmod 600 "${OUT}/env.backup"
echo "  settings  .env copied (contains secrets — keep this directory private)"

cat > "${OUT}/RESTORE.md" <<'RESTOREEOF'
# Restore

1. Copy `env.backup` to `.env` in a fresh checkout and review it.
2. `make up && make migrate`
3. Restore the database:
   `gunzip -c database.sql.gz | docker compose exec -T postgres psql -U tcg -d tcg`
4. Restore images:
   `tar -xzf images.tar.gz -C data/`
5. `make health`

The dump includes inventory, AI corrections, condition assessments, price history and all
external mappings. Reference data (cards, sets) can be rebuilt with `make sync` if needed.
RESTOREEOF

# ── retention ──────────────────────────────────────────────────────────────────────────────
#
# Each backup carries a full copy of the images, so they are about a gigabyte apiece at two
# thousand cards. Left alone this directory fills the disk, and on a Pi the disk is an SD card
# with the database on it — so a backup script with no retention eventually takes out the thing
# it exists to protect.
#
# Oldest first, and never the one just written.
KEEP="${KEEP:-7}"
mapfile -t existing < <(find data/backups -mindepth 1 -maxdepth 1 -type d | sort)
if (( ${#existing[@]} > KEEP )); then
  for old in "${existing[@]:0:$(( ${#existing[@]} - KEEP ))}"; do
    [[ "$old" == "$OUT" ]] && continue
    rm -rf "$old"
    echo "  pruned    ${old##*/}"
  done
fi

echo
echo "Done. ${OUT}"
echo "Keeping the newest ${KEEP} ($(du -sh data/backups | cut -f1) in total). KEEP=N to change."
