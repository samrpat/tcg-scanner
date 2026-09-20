#!/usr/bin/env bash
# Bring the schema up to date, then run whatever was asked for.
#
# Migrations used to be a separate `make migrate` that somebody had to know about. For an
# install anyone can do, the container has to arrive working — a first run that starts cleanly
# and then 500s on every request because no tables exist is the worst kind of broken, because
# it looks like it worked.
#
# Only the API does this. The worker shares the image and would otherwise race it.
set -euo pipefail

if [[ "${RUN_MIGRATIONS:-1}" == "1" && "${1:-}" == "uvicorn" ]]; then
  echo "waiting for the database..."
  for _ in $(seq 1 60); do
    if python -c "
import asyncio, sys
import asyncpg
from app.config import settings
async def go():
    conn = await asyncpg.connect(
        user=settings.postgres_user, password=settings.postgres_password,
        database=settings.postgres_db, host=settings.postgres_host,
        port=settings.postgres_port, timeout=3,
    )
    await conn.close()
asyncio.run(go())
" 2>/dev/null; then
      break
    fi
    sleep 2
  done

  # An advisory lock, so two API containers starting together do not both try to migrate.
  # Postgres hands the lock to one of them; the other waits and then finds nothing to do.
  echo "applying migrations..."
  alembic upgrade head
fi

exec "$@"
