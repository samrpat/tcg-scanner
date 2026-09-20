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

# expect <method> <path> <status> [json-body]
expect() {
  local method=$1 path=$2 want=$3 body=${4:-}
  local got
  if [ -n "$body" ]; then
    got=$(curl -s -o /tmp/smoke.out -w '%{http_code}' -X "$method" "$BASE$path" \
          -H 'content-type: application/json' -d "$body")
  else
    got=$(curl -s -o /tmp/smoke.out -w '%{http_code}' -X "$method" "$BASE$path")
  fi
  if [ "$got" = "$want" ]; then ok "$method $path -> $got"
  else bad "$method $path -> $got (wanted $want)"; head -c 200 /tmp/smoke.out; echo; fi
}

echo "── health & reference ──"
for p in /health /health/detail /health/ready /api/catalog/sets /api/catalog/stats \
         /api/conditioning/conditions /api/conditioning/marketplaces \
         /api/conditioning/rubric /api/conditioning/translations; do
  expect GET "$p" 200
done

echo "── collection ──"
for p in /api/inventory /api/inventory/export.csv /api/inventory/lots \
         /api/capture/pending /api/capture/recent /api/jobs \
         /api/review/approval /api/review/progress /api/review/queue; do
  expect GET "$p" 200
done

echo "── eBay queue ──"
for s in ready blocked listed all; do expect GET "/api/ebay/queue?show=$s" 200; done
expect GET "/api/ebay/queue?show=sideways" 422

SKU=$(curl -s "$BASE/api/inventory" | python3 -c 'import json,sys
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
  code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/inventory/$SKU/listing")
  case "$code" in 200|409) ok "GET /api/inventory/$SKU/listing -> $code";;
                  *) bad "GET /api/inventory/$SKU/listing -> $code";; esac

  echo "── listing drafts for every card ──"
  if curl -s "$BASE/api/ebay/queue?show=all" | python3 scripts/check_queue.py; then
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
if curl -s "$BASE/api/ebay/export.csv?show=ready&location=Smoke+Test" | python3 scripts/check_csv.py; then
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
