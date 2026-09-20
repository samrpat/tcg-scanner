#!/usr/bin/env bash
# Build and push multi-architecture images.
#
# Two architectures matter: arm64 for a Raspberry Pi and an Apple Silicon Mac, amd64 for
# everything else. Building only the one you happen to be on is the classic way to publish an
# image that half your users cannot run.
#
#     TCG_IMAGE=ghcr.io/samrpat/tcg-scanner-api \
#     TCG_WEB_IMAGE=ghcr.io/samrpat/tcg-scanner-web \
#     ./scripts/release.sh v0.1.0
set -euo pipefail

TAG="${1:-}"
if [[ -z "$TAG" ]]; then
  echo "usage: ./scripts/release.sh <tag>   e.g. v0.1.0" >&2
  exit 2
fi

API_IMAGE="${TCG_IMAGE:-ghcr.io/samrpat/tcg-scanner-api}"
WEB_IMAGE="${TCG_WEB_IMAGE:-ghcr.io/samrpat/tcg-scanner-web}"
PLATFORMS="${PLATFORMS:-linux/arm64,linux/amd64}"
REVISION="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

if [[ "$API_IMAGE" == *OWNER* ]]; then
  echo "Set TCG_IMAGE and TCG_WEB_IMAGE to your own registry first." >&2
  exit 2
fi

# The card-back photograph must not be in the build context. It is excluded by
# api/.dockerignore; this is the check that the exclusion is still there, because the failure
# is silent and legal rather than loud and technical.
if ! grep -q "pokemon-back.jpg" api/.dockerignore; then
  echo "api/.dockerignore no longer excludes the card-back photograph. Stopping." >&2
  exit 1
fi
if [[ ! -f api/app/imaging/assets/pokemon-back.npz ]]; then
  echo "No baked descriptors. Run: make bake-reference" >&2
  exit 1
fi

echo "Building $TAG for $PLATFORMS"
docker buildx inspect tcg-builder >/dev/null 2>&1 || docker buildx create --name tcg-builder --use
docker buildx use tcg-builder

docker buildx build \
  --platform "$PLATFORMS" \
  --build-arg "VERSION=$TAG" \
  --build-arg "REVISION=$REVISION" \
  -t "$API_IMAGE:$TAG" -t "$API_IMAGE:latest" \
  --push ./api

docker buildx build \
  --platform "$PLATFORMS" \
  -t "$WEB_IMAGE:$TAG" -t "$WEB_IMAGE:latest" \
  --push ./web

echo
echo "Pushed:"
echo "  $API_IMAGE:$TAG"
echo "  $WEB_IMAGE:$TAG"
echo
echo "Now verify what you actually shipped:  ./scripts/release-check.sh $API_IMAGE:$TAG"
