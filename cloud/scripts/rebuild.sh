#!/usr/bin/env bash
set -Eeuo pipefail

# Zotero-full-translate Cloud safe rebuild.
# Default: removes only this Compose project's containers/orphans, ZFT images and
# build cache, while preserving the named /data volume (zft_data).
# --deep-cache: prune all unused BuildKit cache on this Docker daemon.
# --reset-data: additionally delete this project's persistent ZFT data volume.

DEEP_CACHE=0
RESET_DATA=0
NO_PULL=0
for arg in "$@"; do
  case "$arg" in
    --deep-cache) DEEP_CACHE=1 ;;
    --reset-data) RESET_DATA=1 ;;
    --no-pull) NO_PULL=1 ;;
    -h|--help)
      cat <<'HELP'
Usage: sudo ./scripts/rebuild.sh [--deep-cache] [--reset-data] [--no-pull]

Default behavior:
  - stop/remove current ZFT Compose containers and orphan containers
  - remove Zotero-full-translate Cloud images created by this project when possible
  - clear ordinary BuildKit cache
  - rebuild with --no-cache and recreate the single Zotero-full-translate Cloud container
  - KEEP the persistent /data volume and all translation history

Options:
  --deep-cache  prune all unused BuildKit cache on the Docker daemon
  --reset-data  DELETE ZFT SQLite/PDF/history volume after a typed confirmation
  --no-pull     do not refresh base images
HELP
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Please run with sudo: sudo $0 $*" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f docker-compose.yml && ! -f compose.yml ]]; then
  echo "ERROR: docker-compose.yml/compose.yml not found in $PROJECT_DIR" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker command not found" >&2
  exit 1
fi
docker compose version >/dev/null

PROJECT_NAME="$(basename "$PROJECT_DIR" | tr -c 'a-zA-Z0-9_.-' '-')"
echo "== Zotero-full-translate Cloud rebuild =="
echo "Project: $PROJECT_DIR"
echo "Persistent data: $([[ $RESET_DATA -eq 1 ]] && echo 'WILL BE RESET' || echo 'PRESERVED')"

echo "[1/7] Stop current Compose project and remove orphan containers..."
docker compose down --remove-orphans || true

# Remove legacy ZFT containers that were left by the old multi-container releases,
# but only names matching the historical zft-cloud prefix.
echo "[2/7] Remove legacy ZFT containers..."
mapfile -t LEGACY_IDS < <({ docker ps -aq --filter 'name=^/zft-cloud-' ; docker ps -aq --filter 'name=^/zotero-full-translate-cloud-' ; } || true)
for id in "${LEGACY_IDS[@]:-}"; do
  [[ -n "$id" ]] || continue
  name="$(docker inspect --format '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##' || true)"
  if [[ ( "$name" == zft-cloud-* && "$name" != "zft-cloud" ) || ( "$name" == zotero-full-translate-cloud-* && "$name" != "zotero-full-translate-cloud" ) ]]; then
    echo "  removing $name"
    docker rm -f "$id" >/dev/null || true
  fi
done

if [[ $RESET_DATA -eq 1 ]]; then
  echo
  echo "WARNING: --reset-data deletes ZFT SQLite, PDF files, provider config, quota meters and Translation Memory."
  read -r -p 'Type DELETE-ZFT-DATA to continue: ' CONFIRM
  if [[ "$CONFIRM" != "DELETE-ZFT-DATA" ]]; then
    echo "Data reset cancelled."
    exit 1
  fi
  echo "[3/7] Delete ZFT project data volumes..."
  # Compose -v targets only volumes attached to this Compose project.
  docker compose down -v --remove-orphans || true
else
  echo "[3/7] Keep persistent volumes (recommended)."
fi

echo "[4/7] Remove old ZFT application images..."
# Do not touch unrelated images. Match repository names that begin with zft-cloud.
mapfile -t ZFT_IMAGES < <(docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | awk '$1 ~ /^zft-cloud(:|[-_])/ || $1 ~ /^zft-cloud$/ || $1 ~ /^zotero-full-translate-cloud(:|[-_])/ || $1 ~ /^zotero-full-translate-cloud$/ {print $2}' | sort -u)
for id in "${ZFT_IMAGES[@]:-}"; do
  [[ -n "$id" ]] && docker image rm -f "$id" >/dev/null 2>&1 || true
done

echo "[5/7] Clear Docker build cache..."
if [[ $DEEP_CACHE -eq 1 ]]; then
  docker builder prune -a -f
else
  docker builder prune -f
fi

echo "[6/7] Build Zotero-full-translate Cloud from scratch..."
BUILD_ARGS=(build --no-cache --progress=plain)
[[ $NO_PULL -eq 1 ]] || BUILD_ARGS+=(--pull)
if ! docker compose "${BUILD_ARGS[@]}"; then
  echo "Initial build failed. Clearing BuildKit state and refreshing base images, then retrying once..." >&2
  docker builder prune -a -f || true
  docker buildx prune -a -f 2>/dev/null || true
  if [[ $NO_PULL -eq 0 ]]; then
    docker image rm -f node:20-alpine python:3.12-slim-bookworm >/dev/null 2>&1 || true
    docker pull node:20-alpine
    docker pull python:3.12-slim-bookworm
  fi
  docker compose "${BUILD_ARGS[@]}"
fi

echo "[7/7] Create the new single container and wait for health..."
docker compose up -d --force-recreate --remove-orphans

PORT="${ZFT_PORT:-}"
if [[ -z "$PORT" && -f .env ]]; then
  PORT="$(grep -E '^ZFT_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
fi
PORT="${PORT:-3005}"

healthy=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/tmp/zft-health.json 2>/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done

echo
docker compose ps
if [[ $healthy -eq 1 ]]; then
  echo "Health: $(cat /tmp/zft-health.json)"
  echo "Zotero-full-translate Cloud is ready: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo localhost):${PORT}"
else
  echo "ERROR: health check did not pass on port ${PORT}. Recent logs:" >&2
  docker compose logs --tail=120 zft >&2 || docker compose logs --tail=120 >&2 || true
  exit 1
fi
