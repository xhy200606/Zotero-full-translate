#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

STATE_FILE=".zft-runtime-state"
SERVICE="zft"
FORCE_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --build) FORCE_BUILD=1 ;;
    -h|--help)
      cat <<'HELP'
Usage: ./scripts/update.sh [--build]

Important:
  * this script deploys the source code that is already present in this directory;
    it does NOT download/pull a newer release by itself.

Default:
  * compares runtime-sensitive source hashes with the previous successful update
  * backend/app-only changes -> docker compose restart zft
  * frontend, Dockerfile, requirements or compose changes -> cached build/recreate
  * frontend source hashes are injected into Docker build args to prevent stale UI layers
  * after recreation, verifies the frontend build-id inside the running container
  * preserves Docker layers, BuildKit apt/npm/pip caches and persistent /data

--build  force a normal cached Docker build/recreate (still no prune / no --no-cache)
HELP
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found" >&2; exit 1; }
docker compose version >/dev/null

hash_tree() {
  local target="$1"
  if [[ ! -e "$target" ]]; then printf 'missing'; return; fi
  if [[ -f "$target" ]]; then sha256sum "$target" | awk '{print $1}'; return; fi
  find "$target" -type f \
    ! -path '*/node_modules/*' ! -path '*/dist/*' ! -name '*.log' ! -name '*.pyc' \
    -print0 | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk '{print $1}'
}

BACKEND_HASH="$(printf '%s\n%s\n%s\n' "$(hash_tree backend/app)" "$(hash_tree backend/alembic)" "$(hash_tree backend/alembic.ini)" | sha256sum | awk '{print $1}')"
REQ_HASH="$(hash_tree backend/requirements.txt)"
USER_FE_HASH="$(hash_tree user-frontend)"
ADMIN_FE_HASH="$(hash_tree admin-frontend)"
DOCKER_HASH="$(cat Dockerfile docker-compose.yml 2>/dev/null | sha256sum | awk '{print $1}')"

prev() { awk -F= -v k="$1" '$1==k{print substr($0,index($0,"=")+1)}' "$STATE_FILE" 2>/dev/null | tail -1; }

NEED_BUILD=$FORCE_BUILD
FRONTEND_CHANGED=0
REASON=()
if [[ ! -f "$STATE_FILE" ]]; then
  NEED_BUILD=1; FRONTEND_CHANGED=1; REASON+=("首次运行")
else
  [[ "$(prev requirements)" == "$REQ_HASH" ]] || { NEED_BUILD=1; REASON+=("Python 依赖变化"); }
  [[ "$(prev user_frontend)" == "$USER_FE_HASH" ]] || { NEED_BUILD=1; FRONTEND_CHANGED=1; REASON+=("用户前端变化"); }
  [[ "$(prev admin_frontend)" == "$ADMIN_FE_HASH" ]] || { NEED_BUILD=1; FRONTEND_CHANGED=1; REASON+=("管理前端变化"); }
  [[ "$(prev docker)" == "$DOCKER_HASH" ]] || { NEED_BUILD=1; REASON+=("Docker/Compose 变化"); }
fi
[[ $FORCE_BUILD -eq 1 ]] && FRONTEND_CHANGED=1

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  NEED_BUILD=1; REASON+=("容器未运行")
fi

if docker compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "== ZFT pre-upgrade database backup =="
  docker compose exec -T "$SERVICE" python - <<'PYBACKUP' || { echo "ERROR: database backup failed" >&2; exit 1; }
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
src = Path("/data/zft.db")
if src.is_file():
    outdir = Path("/data/backups")
    outdir.mkdir(parents=True, exist_ok=True)
    dst = outdir / ("zft-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".db")
    source = sqlite3.connect(str(src), timeout=30)
    target = sqlite3.connect(str(dst), timeout=30)
    try:
        source.backup(target)
    finally:
        target.close(); source.close()
    backups = sorted(outdir.glob("zft-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[10:]:
        old.unlink(missing_ok=True)
    print(dst)
else:
    print("fresh database: no backup needed")
PYBACKUP
fi

if [[ $NEED_BUILD -eq 1 ]]; then
  echo "== ZFT cached image update =="
  echo "原因: ${REASON[*]:-手工要求构建}"
  if [[ $FRONTEND_CHANGED -eq 1 ]]; then
    echo "用户前端 build-id: ${USER_FE_HASH:0:12}"
    echo "管理前端 build-id: ${ADMIN_FE_HASH:0:12}"
  fi
  echo "保留 Docker/BuildKit 缓存；不会执行 prune、--no-cache 或删除镜像。"
  ZFT_USER_FRONTEND_REV="$USER_FE_HASH" \
  ZFT_ADMIN_FRONTEND_REV="$ADMIN_FE_HASH" \
    DOCKER_BUILDKIT=1 docker compose build --progress=plain "$SERVICE"

  # The image tag remains stable, so force recreation to guarantee that the
  # container uses the image produced by the build above.
  ZFT_USER_FRONTEND_REV="$USER_FE_HASH" \
  ZFT_ADMIN_FRONTEND_REV="$ADMIN_FE_HASH" \
    docker compose up -d --force-recreate --remove-orphans --no-deps "$SERVICE"
else
  if [[ "$(prev backend)" != "$BACKEND_HASH" ]]; then
    echo "== ZFT backend fast update =="
    echo "仅 backend/app 或 migration 变化。由于源码已 bind-mount，直接重启，不构建 Docker。"
    docker compose restart "$SERVICE"
  else
    echo "运行时代码未变化；确保容器处于启动状态。"
    docker compose up -d "$SERVICE"
  fi
fi

PORT="${ZFT_PORT:-}"
if [[ -z "$PORT" && -f .env ]]; then
  PORT="$(grep -E '^ZFT_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
fi
PORT="${PORT:-3005}"

healthy=0
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/tmp/zft-health.json 2>/dev/null; then healthy=1; break; fi
  sleep 2
done
if [[ $healthy -ne 1 ]]; then
  echo "ERROR: health check failed; preserving previous Docker caches for diagnosis." >&2
  docker compose logs --tail=120 "$SERVICE" >&2 || true
  exit 1
fi

if [[ $NEED_BUILD -eq 1 ]]; then
  RUNNING_USER_FE="$(docker compose exec -T "$SERVICE" sh -lc 'cat /app/static/user/build-id.txt 2>/dev/null || true' | tr -d '\r\n')"
  RUNNING_ADMIN_FE="$(docker compose exec -T "$SERVICE" sh -lc 'cat /app/static/admin/build-id.txt 2>/dev/null || true' | tr -d '\r\n')"
  if [[ "$RUNNING_USER_FE" != "$USER_FE_HASH" || "$RUNNING_ADMIN_FE" != "$ADMIN_FE_HASH" ]]; then
    echo "ERROR: frontend deployment verification failed." >&2
    echo "expected user=${USER_FE_HASH:0:12}, running user=${RUNNING_USER_FE:0:12}" >&2
    echo "expected admin=${ADMIN_FE_HASH:0:12}, running admin=${RUNNING_ADMIN_FE:0:12}" >&2
    echo "The container is not serving the frontend built from the current source tree." >&2
    exit 1
  fi
  echo "Frontend verified: user=${USER_FE_HASH:0:12}, admin=${ADMIN_FE_HASH:0:12}"
fi

cat > "$STATE_FILE" <<EOFSTATE
backend=$BACKEND_HASH
requirements=$REQ_HASH
user_frontend=$USER_FE_HASH
admin_frontend=$ADMIN_FE_HASH
docker=$DOCKER_HASH
EOFSTATE

echo "Health: $(cat /tmp/zft-health.json)"
echo "Update complete. If a browser tab was already open, reload it once to fetch the new index bundle."
