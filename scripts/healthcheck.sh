#!/usr/bin/env bash
# Report per-dependency health in a form a human can act on (REQ-OPS-001).
set -uo pipefail

PORT="${WEB_PORT:-8080}"
[[ -f .env ]] && PORT="$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 || echo "$PORT")"
PORT="${PORT:-8080}"
BASE="http://localhost:${PORT}"

printf '\n  Checking %s\n\n' "$BASE"

# Give the API time to come up rather than reporting "unreachable" at a stack that is three
# seconds from ready. `make up` calls this immediately after starting the containers, and a
# cold API — Python import, engine, first connection — regularly takes twenty seconds on a Pi.
# WAIT=0 skips the waiting, for a genuine is-it-up-right-now check.
WAIT="${WAIT:-60}"
deadline=$(( $(date +%s) + WAIT ))
until curl -fsS --max-time 5 "${BASE}/health" >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    printf '  api            unreachable\n'
    printf '\n  The stack may still be starting. Try: make logs\n\n'
    exit 1
  fi
  sleep 2
done

BODY="$(curl -fsS --max-time 10 "${BASE}/health/detail" 2>/dev/null)"
if [[ -z "$BODY" ]]; then
  printf '  api            up, but /health/detail returned nothing\n\n'
  exit 1
fi

python3 - "$BODY" <<'PYEOF' 2>/dev/null || { printf '%s\n' "$BODY"; exit 0; }
import json, sys
data = json.loads(sys.argv[1])
for name, check in data["checks"].items():
    if check.get("ok"):
        detail = f"{check['ms']} ms" if check.get("ms") is not None else check.get("backend", "ok")
        print(f"  {name:<14} ok    {detail}")
    else:
        print(f"  {name:<14} DOWN  {check.get('error', '')[:70]}")
print()
print(f"  worker tags    {', '.join(data['worker_tags']) or 'none'}")
print(f"  currency       {data['display_currency']}")
print()
print(f"  overall        {data['status']}")
print()
sys.exit(0 if data["status"] == "ok" else 1)
PYEOF
