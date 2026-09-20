#!/usr/bin/env bash
# Inspect a built image and refuse the ones that should not be published.
#
# Checks the artifact rather than the source, because the things that go wrong here go wrong
# between the two: a .dockerignore that stopped matching, a stale layer, a secret picked up
# from a build context.
set -uo pipefail

IMAGE="${1:-tcg-scanner-api:local}"
fails=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails+1)); }

echo "Checking $IMAGE"

run() { docker run --rm --entrypoint sh "$IMAGE" -c "$1" 2>/dev/null; }

# 1. No card artwork. This one is a licensing question, not a technical one: the photograph is
#    Nintendo / Creatures / GAME FREAK's, and a published image must carry the ORB descriptors
#    computed from it instead.
if [ -n "$(run 'ls /srv/app/imaging/assets/*.jpg 2>/dev/null')" ]; then
  bad "card-back photograph is in the image — check api/.dockerignore"
else
  ok "no card artwork in the image"
fi
if [ -n "$(run 'ls /srv/app/imaging/assets/pokemon-back.npz 2>/dev/null')" ]; then
  ok "card-back descriptors are present"
else
  bad "no card-back descriptors — run: make bake-reference"
fi

# 2. No secrets. A .env swept in from the build context would ship the operator's database
#    password to everyone who pulls the image.
if [ -n "$(run 'ls -a /srv | grep -E "^\.env" 2>/dev/null')" ]; then
  bad ".env is inside the image"
else
  ok "no .env in the image"
fi

# 3. It runs as nobody in particular.
user=$(docker inspect "$IMAGE" --format '{{.Config.User}}' 2>/dev/null)
if [ "$user" = "root" ] || [ -z "$user" ]; then
  bad "image runs as root (USER is '${user:-unset}')"
else
  ok "runs as non-root ($user)"
fi

# 4. Authentication defaults on. Shipping a build that is open by default would hand every
#    installation's collection to its local network.
default_auth=$(run 'python -c "from app.config import Settings; print(Settings().auth_required)"')
if [ "$default_auth" = "True" ]; then
  ok "authentication defaults on"
else
  bad "AUTH_REQUIRED does not default on (got '${default_auth:-nothing}')"
fi

# 5. It knows what it is.
version=$(docker inspect "$IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null)
if [ -n "$version" ] && [ "$version" != "dev" ]; then
  ok "labelled $version"
else
  bad "no version label (got '${version:-nothing}') — build through scripts/release.sh"
fi

echo
if [ "$fails" -eq 0 ]; then
  printf '\033[32mSafe to publish.\033[0m\n'
else
  printf '\033[31m%s problem(s). Do not publish.\033[0m\n' "$fails"
  exit 1
fi
