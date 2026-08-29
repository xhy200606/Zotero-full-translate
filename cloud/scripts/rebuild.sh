#!/usr/bin/env bash
set -Eeuo pipefail


SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

FRESH=0
RESET_DATA=0
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=1 ;;
    --reset-data) RESET_DATA=1 ;;
    -h|--help)
      cat <<'HELP'
Usage: sudo ./scripts/rebuild.sh [--fresh] [--reset-data]

Without --fresh:
  normal cached Docker rebuild; preserves images, BuildKit cache and /data.

--fresh:
  emergency from-scratch rebuild. Removes only ZFT application image and prunes
  unused build cache, then builds with --no-cache. This is deliberately NOT the
  normal upgrade path.

--reset-data:
  additionally deletes the persistent ZFT data volume after typed confirmation.
HELP
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Please run with sudo" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found" >&2; exit 1; }
docker compose version >/dev/null

echo "== Zotero-full-translate Cloud rebuild =="
if [[ $RESET_DATA -eq 1 ]]; then
  echo "WARNING: this will delete SQLite, PDFs, account/provider configuration and history."
  read -r -p 'Type DELETE-ZFT-DATA to continue: ' CONFIRM
  [[ "$CONFIRM" == "DELETE-ZFT-DATA" ]] || { echo "Cancelled."; exit 1; }
  docker compose down -v --remove-orphans || true
else
  docker compose down --remove-orphans || true
fi

if [[ $FRESH -eq 1 ]]; then
  echo "Fresh rebuild requested: clearing ZFT image and unused build cache."
  docker image rm -f zotero-full-translate-cloud:2.3.2 >/dev/null 2>&1 || true
  docker builder prune -f
  DOCKER_BUILDKIT=1 docker compose build --no-cache --progress=plain zft
else
  echo "Cached rebuild: no prune, no --no-cache, no forced base-image pull."
  DOCKER_BUILDKIT=1 docker compose build --progress=plain zft
fi

docker compose up -d --remove-orphans zft

hash_tree() {
  local target="$1"
  if [[ -f "$target" ]]; then sha256sum "$target" | awk '{print $1}'; return; fi
  find "$target" -type f ! -path '*/node_modules/*' ! -path '*/dist/*' ! -name '*.log' ! -name '*.pyc' -print0 | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk '{print $1}'
}
cat > .zft-runtime-state <<EOF
backend=$(hash_tree backend/app)
requirements=$(hash_tree backend/requirements.txt)
user_frontend=$(hash_tree user-frontend)
admin_frontend=$(hash_tree admin-frontend)
docker=$(cat Dockerfile docker-compose.yml | sha256sum | awk '{print $1}')
EOF

PORT="${ZFT_PORT:-}"
if [[ -z "$PORT" && -f .env ]]; then PORT="$(grep -E '^ZFT_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '\r' || true)"; fi
PORT="${PORT:-3005}"
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/tmp/zft-health.json 2>/dev/null; then
    echo "Health: $(cat /tmp/zft-health.json)"
    echo "Rebuild complete."
    exit 0
  fi
  sleep 2
done
echo "ERROR: health check failed" >&2
docker compose logs --tail=120 zft >&2 || true
exit 1
