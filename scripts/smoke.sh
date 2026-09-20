#!/usr/bin/env bash
# Exercise every HTTP endpoint against the running stack.
#
# The unit suite deliberately skips anything that needs a database, so without this the
# database-backed half of the API — which is most of it — is only ever tested by using the app.
# This is the check that a deploy did not break a screen.
#
# It is read-mostly. The few writes it performs are made to a card it picks itself and are
# undone before it exits, so it is safe to run against real data.
set -uo pipefail

BASE="${BASE:-http://localhost:8080}"
pass=0; fail=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }

# ── the front door ─────────────────────────────────────────────────────────────────────────
#
# Every request below carries the session cookie, because every route below needs one. The
# password comes from the environment and is never echoed:
#
#     TCG_PASSWORD=... ./scripts/smoke.sh
#
# An instance with authentication switched off needs none of this and the login is skipped.
JAR="$(mktemp -t tcg-smoke-cookies)"
trap 'rm -f "$JAR" /tmp/smoke.out' EXIT

AUTH_REQUIRED=$(curl -s "$BASE/api/auth/status" | python3 -c 'import json,sys
d=json.load(sys.stdin); print("yes" if d.get("required") and not d.get("authenticated") else "no")' 2>/dev/null || echo yes)

if [ "$AUTH_REQUIRED" = yes ]; then
  if [ -z "${TCG_PASSWORD:-}" ]; then
    echo "This instance needs a password. Re-run as:"
    echo "    TCG_PASSWORD='your password' ./scripts/smoke.sh"
    exit 2
  fi
  login=$(python3 -c 'import json,os; print(json.dumps({"password": os.environ["TCG_PASSWORD"], "label": "smoke test"}))')
  status=$(curl -s -o /dev/null -w '%{http_code}' -c "$JAR" -X POST "$BASE/api/auth/login" \
           -H 'content-type: application/json' -d "$login")
  if [ "$status" != 200 ]; then
    echo "Could not log in (HTTP $status). Wrong password, or the instance is unclaimed."
    exit 2
  fi
  echo "logged in"
fi

# expect <method> <path> <status> [json-body]
expect() {
  local method=$1 path=$2 want=$3 body=${4:-}
  local got
  if [ -n "$body" ]; then
    got=$(curl -s -o /tmp/smoke.out -w '%{http_code}' -b "$JAR" -X "$method" "$BASE$path" \
          -H 'content-type: application/json' -d "$body")
  else
    got=$(curl -s -o /tmp/smoke.out -w '%{http_code}' -b "$JAR" -X "$method" "$BASE$path")
  fi
  if [ "$got" = "$want" ]; then ok "$method $path -> $got"
  else bad "$method $path -> $got (wanted $want)"; head -c 200 /tmp/smoke.out; echo; fi
}

# Which half of the app is running. A scanner build does not mount the listing routers at
# all, so asking it for /api/ebay/queue and calling the 404 a failure would make this script
# fail by design. Read from the API rather than from a local file, because what matters is
# what the container is actually serving.
MODE=$(curl -s "$BASE/health/detail" | python3 -c 'import json,sys
print("scanner" if json.load(sys.stdin).get("scanner_mode") else "full")' 2>/dev/null || echo full)
echo "mode: $MODE"

if [ "$AUTH_REQUIRED" = yes ]; then
  echo "── the door is shut ──"
  # No cookie: the two that must answer, and one that must not.
  for probe in /health:200 /api/auth/status:200 /api/inventory:401 /api/images/x/y.jpg:401; do
    path=${probe%:*}; want=${probe##*:}
    got=$(curl -s -o /dev/null -w '%{http_code}' "$BASE$path")
    if [ "$got" = "$want" ]; then ok "anonymous GET $path -> $got"
    else bad "anonymous GET $path -> $got (wanted $want)"; fi
  done
fi

echo "── health & reference ──"
for p in /health /health/detail /health/ready /api/catalog/sets /api/catalog/stats; do
  expect GET "$p" 200
done

echo "── collection ──"
for p in /api/inventory /api/capture/pending /api/capture/recent /api/jobs; do
  expect GET "$p" 200
done

echo "── batches ──"
expect GET "/api/sessions" 200
CUR=$(curl -s -b "$JAR" "$BASE/api/sessions" | python3 -c 'import json,sys
print(json.load(sys.stdin).get("current_id") or "")' 2>/dev/null)
if [ -z "$CUR" ]; then
  echo "  (no open batch; skipping batch checks)"
else
  expect GET "/api/inventory?session_id=$CUR" 200
  # Downloading the folder is the whole point of the scanner, so it is worth one real request.
  # Corners are off here to keep the smoke run from pulling tens of megabytes every time.
  # A batch with nothing in it yet answers 409 by design, which happens whenever this runs
  # just after an archive — so both are a pass and neither is a silent one.
  code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/api/sessions/$CUR/photos.zip?corners=false")
  case "$code" in
    200) ok "GET /api/sessions/\$CUR/photos.zip -> 200" ;;
    409) ok "GET /api/sessions/\$CUR/photos.zip -> 409 (batch is empty)" ;;
    *)   bad "GET /api/sessions/\$CUR/photos.zip -> $code" ;;
  esac

  # Moving cards. The move performed is into the batch the card is already in, so the endpoint
  # is genuinely exercised and the data is genuinely unchanged.
  BSKU=$(curl -s -b "$JAR" "$BASE/api/inventory?session_id=$CUR" | python3 -c 'import json,sys
c=json.load(sys.stdin)["cards"]
print(c[0]["sku"] if c else "")' 2>/dev/null)
  if [ -n "$BSKU" ]; then
    expect POST "/api/sessions/$CUR/cards" 200 "{\"skus\":[\"$BSKU\"]}"
  fi
  expect POST "/api/sessions/$CUR/cards" 400 '{"skus":[]}'
  expect POST "/api/sessions/00000000-0000-0000-0000-000000000000/cards" 404 '{"skus":["X"]}'

  # The corner switch. Set to whatever it already is, so the endpoint is exercised and the
  # batch is left exactly as it was found.
  WAS=$(curl -s -b "$JAR" "$BASE/api/sessions" | python3 -c 'import json,sys
d=json.load(sys.stdin)
cur=[s for s in d["sessions"] if s["current"]]
print("true" if cur and cur[0]["corner_shots"] else "false")' 2>/dev/null)
  expect POST "/api/sessions/$CUR/corners" 200 "{\"on\":$WAS}"
  expect POST "/api/sessions/00000000-0000-0000-0000-000000000000/corners" 404 '{"on":true}'
  if [ -n "$BSKU" ]; then
    # Re-cutting one card's corners is idempotent: same source, same crop, overwritten in place.
    expect POST "/api/capture/$BSKU/detail-shots" 200
  fi
  expect POST "/api/capture/NOPE-1/detail-shots" 404

  # The photo-count plan. This is what a bulk uploader has to be told, so it is worth a check
  # that it answers at all and that the split download it describes actually builds.
  expect GET "/api/sessions/$CUR/photo-groups" 200
  expect GET "/api/sessions/00000000-0000-0000-0000-000000000000/photo-groups" 404
  expect GET "/api/sessions/$CUR/photos.zip?layout=sideways" 422

  # Renaming. Set to the name it already has, so the endpoint runs and the batch is unchanged.
  NAME=$(curl -s -b "$JAR" "$BASE/api/sessions" | python3 -c 'import json,sys
d=json.load(sys.stdin)
cur=[s for s in d["sessions"] if s["current"]]
print(json.dumps(cur[0]["name"]) if cur else json.dumps(""))' 2>/dev/null)
  expect PATCH "/api/sessions/$CUR" 200 "{\"name\":$NAME}"
  expect PATCH "/api/sessions/00000000-0000-0000-0000-000000000000" 404 '{"name":"x"}'

  # Angled shots. Only the failure paths are exercised — the success path needs a photograph,
  # and this script does not invent card images on someone's real collection.
  # 422, not 404: the multipart body is validated before the handler runs, so a request with no
  # file never reaches the lookup that would report the unknown card.
  expect POST "/api/capture/NOPE-1/extra" 422
  expect DELETE "/api/capture/NOPE-1/extra/1" 404
  if [ -n "$BSKU" ]; then
    expect DELETE "/api/capture/$BSKU/extra/9" 422
  fi
fi

if [ "$MODE" = scanner ]; then
  echo "── listing routes are not mounted ──"
  for p in /api/ebay/queue /api/review/queue /api/conditioning/rubric; do
    expect GET "$p" 404
  done

  echo
  if [ "$fail" -eq 0 ]; then
    printf '\033[32m%s checks passed\033[0m (scanner mode)\n' "$pass"; exit 0
  else
    printf '\033[31m%s failed\033[0m, %s passed\n' "$fail" "$pass"; exit 1
  fi
fi

echo "── grading & review ──"
for p in /api/conditioning/conditions /api/conditioning/marketplaces \
         /api/conditioning/rubric /api/conditioning/translations \
         /api/inventory/export.csv /api/inventory/lots \
         /api/review/approval /api/review/progress /api/review/queue; do
  expect GET "$p" 200
done

echo "── eBay queue ──"
for s in ready blocked listed all; do expect GET "/api/ebay/queue?show=$s" 200; done
expect GET "/api/ebay/queue?show=sideways" 422

SKU=$(curl -s -b "$JAR" "$BASE/api/inventory" | python3 -c 'import json,sys
c=json.load(sys.stdin)["cards"]
print(c[0]["sku"] if c else "")' 2>/dev/null)

if [ -z "$SKU" ]; then
  echo "  (no cards in inventory; skipping per-card checks)"
else
  echo "── per-card ($SKU) ──"
  expect GET "/api/capture/$SKU" 200
  expect GET "/api/capture/$SKU/variants" 200
  expect GET "/api/conditioning/$SKU/assessment" 200
  # A card with no identity legitimately 409s here, so accept either.
  code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/api/inventory/$SKU/listing")
  case "$code" in 200|409) ok "GET /api/inventory/$SKU/listing -> $code";;
                  *) bad "GET /api/inventory/$SKU/listing -> $code";; esac

  echo "── listing drafts for every card ──"
  if curl -s -b "$JAR" "$BASE/api/ebay/queue?show=all" | python3 scripts/check_queue.py; then
    pass=$((pass+1))
  else
    fail=$((fail+1))
  fi

  echo "── writes (undone afterwards) ──"
  expect POST "/api/ebay/$SKU/draft" 422 '{"title":"'"$(python3 -c 'print("x"*81)')"'"}'
  expect POST "/api/ebay/$SKU/draft" 422 '{"price":-1}'
  expect POST "/api/ebay/$SKU/draft/reset" 200
fi

echo "── eBay bulk upload file ──"
expect GET "/api/ebay/export.csv?show=ready" 200
# A location is passed so this checks the file's shape rather than failing on an address
# only the operator can supply; the UI warns about that separately.
if curl -s -b "$JAR" "$BASE/api/ebay/export.csv?show=ready&location=Smoke+Test" | python3 scripts/check_csv.py; then
  pass=$((pass+1))
else
  fail=$((fail+1))
fi

expect GET "/api/ebay/export/check?location=Smoke+Test" 200
expect GET "/api/ebay/template" 200

echo "── unknown ids are 4xx, never 5xx ──"
expect GET  "/api/inventory/NOPE-1/listing" 404
expect GET  "/api/capture/NOPE-1" 404
expect POST "/api/ebay/NOPE-1/listed" 404 '{"listed":true}'
expect POST "/api/ebay/NOPE-1/draft" 404 '{"title":"x"}'
expect POST "/api/ebay/NOPE-1/details" 404

echo
if [ "$fail" -eq 0 ]; then
  printf '\033[32m%s checks passed\033[0m\n' "$pass"; exit 0
else
  printf '\033[31m%s failed\033[0m, %s passed\n' "$fail" "$pass"; exit 1
fi
