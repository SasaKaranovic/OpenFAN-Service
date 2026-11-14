#!/usr/bin/env bash
# Build the Docker image locally.
#
# Every build is stamped with a version: the build date (UTC, yyyy-mm-dd) and the git
# short hash ("-dirty" if tracked files have uncommitted changes), e.g. v2026-11-13 [a1b2c3d].
# It is shown in the web UI footer. The image is tagged NAME:dev and NAME:<date>-<hash>.
#
#   scripts/docker-build.sh                    # -> openfan-service:dev for this machine
#   scripts/docker-build.sh --tag myname:test
#   scripts/docker-build.sh --no-cache
#   scripts/docker-build.sh --multiarch        # also build linux/amd64 + linux/arm64 (not pushed,
#                                              # only checks that both build; needs docker buildx)
set -euo pipefail
cd "$(dirname "$0")/.."

TAG=openfan-service:dev; MULTI=0; EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:?}"; shift ;;
    --multiarch) MULTI=1 ;;
    --no-cache) EXTRA+=(--no-cache) ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

command -v docker >/dev/null 2>&1 || { echo "error: docker not found" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "error: the Docker daemon is not running (or you lack permission: try sudo or add yourself to the docker group)" >&2; exit 1; }

VERSION="$(date -u +%Y-%m-%d)"
GIT_HASH=unknown
if command -v git >/dev/null 2>&1 && GIT_SHORT="$(git rev-parse --short HEAD 2>/dev/null)"; then
  GIT_HASH="$GIT_SHORT"
  [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ] && GIT_HASH="$GIT_HASH-dirty"
fi
VERSION_TAG="${TAG%%:*}:$VERSION-$GIT_HASH"
BUILD_ARGS=(--build-arg "OPENFAN_VERSION=$VERSION" --build-arg "OPENFAN_GIT_HASH=$GIT_HASH")

echo "Building $TAG (version v$VERSION [$GIT_HASH]) ..."
docker build ${EXTRA[@]+"${EXTRA[@]}"} "${BUILD_ARGS[@]}" -t "$TAG" -t "$VERSION_TAG" .
echo "Built $TAG and $VERSION_TAG"

if [ "$MULTI" = 1 ]; then
  echo "Checking multi-arch build (linux/amd64, linux/arm64). Nothing is pushed."
  if ! docker buildx inspect openfan-multiarch >/dev/null 2>&1; then
    docker buildx create --name openfan-multiarch --driver docker-container >/dev/null
  fi
  docker buildx build --builder openfan-multiarch --platform linux/amd64,linux/arm64 ${EXTRA[@]+"${EXTRA[@]}"} "${BUILD_ARGS[@]}" -t "$TAG" .
  echo "Multi-arch build OK (result kept in the build cache only)."
fi

echo
echo "Run it with: scripts/docker-run.sh --mode demo"
