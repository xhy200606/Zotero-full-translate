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

ensure_config_secret() {
  local env_file=".env"
  local placeholder1="change-me-with-openssl-rand-hex-32"
  local placeholder2="change-me-with-a-different-openssl-rand-hex-32"
  local chosen="" recovered="" generated="" backup=""

  if [[ ! -f "$env_file" ]]; then
    if [[ -f .env.example ]]; then
      cp .env.example "$env_file"
      echo "Created .env from .env.example."
    else
      : > "$env_file"
      chmod 600 "$env_file" 2>/dev/null || true
      echo "Created empty .env."
    fi
  fi

  # Read every ZFT_CONFIG_SECRET entry and prefer an existing real value over
  # placeholders. This also repairs old .env files containing duplicate keys
  # such as a real 64-char secret followed by the 34-char example placeholder.
  chosen="$(python3 - "$env_file" <<'PYREADSECRET'
from pathlib import Path
import sys
p = Path(sys.argv[1])
placeholders = {
    "", "change-me", "change-me-with-openssl-rand-hex-32",
    "change-me-with-a-different-openssl-rand-hex-32",
}
real = []
for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
    if not raw.startswith("ZFT_CONFIG_SECRET="):
        continue
    value = raw.split("=", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if value not in placeholders:
        real.append(value)
strong = [v for v in real if len(v) >= 32]
if strong:
    print(strong[-1], end="")
elif real:
    print(real[-1], end="")
PYREADSECRET
)"

  if [[ -z "$chosen" ]]; then
    # Recover only non-placeholder material from an existing container.
    recovered="$(docker inspect zotero-full-translate-cloud --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
      | grep -E '^ZFT_CONFIG_SECRET=' | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
    recovered="$(printf '%s' "$recovered" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    case "$recovered" in
      ""|change-me|"$placeholder1"|"$placeholder2") recovered="" ;;
    esac
    if [[ -n "$recovered" ]]; then
      chosen="$recovered"
      echo "Recovered existing non-placeholder ZFT_CONFIG_SECRET from the running container."
    fi
  fi

  if [[ -z "$chosen" ]]; then
    if command -v openssl >/dev/null 2>&1; then
      chosen="$(openssl rand -hex 32)"
    elif command -v python3 >/dev/null 2>&1; then
      chosen="$(python3 - <<'PYSECRET'
import secrets
print(secrets.token_hex(32))
PYSECRET
)"
    else
      chosen="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    fi
    echo "Generated a new ZFT_CONFIG_SECRET for Provider credential encryption."
    echo "IMPORTANT: keep this .env file/backups; changing this secret later makes existing encrypted Provider secrets unreadable."
  elif (( ${#chosen} < 32 )); then
    echo "WARNING: existing non-placeholder ZFT_CONFIG_SECRET is shorter than 32 characters; preserving it to avoid breaking encrypted Provider secrets." >&2
  fi

  # Normalize duplicates to exactly one entry. Do not print the secret.
  backup=".env.backup-before-config-secret-$(date +%Y%m%dT%H%M%S)"
  cp -p "$env_file" "$backup" 2>/dev/null || cp "$env_file" "$backup"
  ZFT_SECRET_TO_WRITE="$chosen" python3 - "$env_file" <<'PYWRITESECRET'
from pathlib import Path
import os, sys
p = Path(sys.argv[1])
secret = os.environ["ZFT_SECRET_TO_WRITE"]
lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
out = [line for line in lines if not line.startswith("ZFT_CONFIG_SECRET=")]
if out and out[-1] != "":
    out.append("")
out.append("ZFT_CONFIG_SECRET=" + secret)
p.write_text("\n".join(out) + "\n", encoding="utf-8")
PYWRITESECRET
  chmod 600 "$env_file" 2>/dev/null || true
  echo "ZFT_CONFIG_SECRET normalized in .env: one non-placeholder entry, length=${#chosen} (backup: $backup)."
}

verify_config_secret() {
  local env_file=".env"
  local host_sig="" container_sig=""
  host_sig="$(python3 - "$env_file" <<'PYHOSTSIG'
from pathlib import Path
import hashlib, sys
vals=[]
for line in Path(sys.argv[1]).read_text(encoding='utf-8', errors='replace').splitlines():
    if line.startswith('ZFT_CONFIG_SECRET='):
        v=line.split('=',1)[1].strip()
        if len(v)>=2 and v[0]==v[-1] and v[0] in "\"'": v=v[1:-1].strip()
        vals.append(v)
v=vals[-1] if vals else ''
print(f"{len(v)}:{hashlib.sha256(v.encode()).hexdigest()[:16]}", end='')
PYHOSTSIG
)"
  container_sig="$(docker compose exec -T "$SERVICE" python - <<'PYCONTSIG' 2>/dev/null || true
import hashlib, os
v=os.getenv('ZFT_CONFIG_SECRET','').strip()
placeholders={'','change-me','change-me-with-openssl-rand-hex-32','change-me-with-a-different-openssl-rand-hex-32'}
status='placeholder' if v in placeholders else 'ok'
print(f"{status}:{len(v)}:{hashlib.sha256(v.encode()).hexdigest()[:16]}", end='')
PYCONTSIG
)"
  if [[ "$container_sig" != "ok:$host_sig" ]]; then
    echo "ERROR: running container did not load the normalized ZFT_CONFIG_SECRET." >&2
    echo "host signature=$host_sig; container signature=$container_sig" >&2
    echo "Force-recreate the zft service and check for duplicate compose/env configuration." >&2
    return 1
  fi
  echo "Provider-secret encryption verified (length=${host_sig%%:*}; fingerprint=${host_sig#*:})."
}

ensure_public_admin_bind() {
  local env_file=".env"
  local current=""
  current="$(grep -E '^ZFT_ADMIN_BIND=' "$env_file" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r' | xargs || true)"
  if [[ -z "$current" ]]; then
    printf '\nZFT_ADMIN_BIND=0.0.0.0\n' >> "$env_file"
    echo "Configured ZFT_ADMIN_BIND=0.0.0.0 so the 3006 admin port is reachable outside localhost."
  elif [[ "$current" == "127.0.0.1" || "$current" == "localhost" ]]; then
    python3 - "$env_file" <<'PYADMINBIND'
from pathlib import Path
import sys
p=Path(sys.argv[1])
lines=p.read_text(encoding='utf-8', errors='replace').splitlines()
out=[]
seen=False
for line in lines:
    if line.startswith('ZFT_ADMIN_BIND='):
        if not seen:
            out.append('ZFT_ADMIN_BIND=0.0.0.0')
            seen=True
        continue
    out.append(line)
if not seen:
    out.append('ZFT_ADMIN_BIND=0.0.0.0')
p.write_text('\n'.join(out)+'\n', encoding='utf-8')
PYADMINBIND
    echo "Migrated the old stock ZFT_ADMIN_BIND=$current to 0.0.0.0; port 3006 can now be published by Docker."
  fi
}

ensure_config_secret
ensure_public_admin_bind

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
ENV_HASH="$(sha256sum .env | awk '{print $1}')"

prev() { awk -F= -v k="$1" '$1==k{print substr($0,index($0,"=")+1)}' "$STATE_FILE" 2>/dev/null | tail -1; }

NEED_BUILD=$FORCE_BUILD
NEED_RECREATE=0
FRONTEND_CHANGED=0
REASON=()
if [[ ! -f "$STATE_FILE" ]]; then
  NEED_BUILD=1; FRONTEND_CHANGED=1; REASON+=("首次运行")
else
  [[ "$(prev requirements)" == "$REQ_HASH" ]] || { NEED_BUILD=1; REASON+=("Python 依赖变化"); }
  [[ "$(prev user_frontend)" == "$USER_FE_HASH" ]] || { NEED_BUILD=1; FRONTEND_CHANGED=1; REASON+=("用户前端变化"); }
  [[ "$(prev admin_frontend)" == "$ADMIN_FE_HASH" ]] || { NEED_BUILD=1; FRONTEND_CHANGED=1; REASON+=("管理前端变化"); }
  [[ "$(prev docker)" == "$DOCKER_HASH" ]] || { NEED_BUILD=1; REASON+=("Docker/Compose 变化"); }
  [[ "$(prev env)" == "$ENV_HASH" ]] || { NEED_RECREATE=1; REASON+=(".env 变化"); }
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
  if [[ $NEED_RECREATE -eq 1 ]]; then
    echo "== ZFT environment update =="
    echo ".env 已变化；强制重新创建容器以重新加载 env_file。"
    docker compose up -d --force-recreate --remove-orphans --no-deps "$SERVICE"
  elif [[ "$(prev backend)" != "$BACKEND_HASH" ]]; then
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

ADMIN_PORT="${ZFT_ADMIN_PORT:-}"
if [[ -z "$ADMIN_PORT" && -f .env ]]; then
  ADMIN_PORT="$(grep -E '^ZFT_ADMIN_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '\r' || true)"
fi
ADMIN_PORT="${ADMIN_PORT:-3006}"
if ! curl -fsS "http://127.0.0.1:${ADMIN_PORT}/health" >/tmp/zft-admin-health.json 2>/dev/null; then
  echo "ERROR: admin port ${ADMIN_PORT} is not reachable on the host." >&2
  echo "Check ZFT_ADMIN_BIND/ZFT_ADMIN_PORT and host firewall rules." >&2
  exit 1
fi
RUNNING_ADMIN_HTTP_REV="$(curl -fsS "http://127.0.0.1:${ADMIN_PORT}/build-id.txt" 2>/dev/null | tr -d '\r\n' || true)"
if [[ "$RUNNING_ADMIN_HTTP_REV" != "$ADMIN_FE_HASH" ]]; then
  echo "ERROR: port ${ADMIN_PORT} is reachable but is not serving the current admin frontend." >&2
  echo "expected admin=${ADMIN_FE_HASH:0:12}, served=${RUNNING_ADMIN_HTTP_REV:0:12}" >&2
  exit 1
fi
echo "Admin port verified: 0.0.0.0:${ADMIN_PORT} -> admin frontend ${ADMIN_FE_HASH:0:12}."

# Verify the effective Provider encryption secret without printing it.
HOST_SECRET_META="$(python3 - <<'PYHOSTMETA'
from pathlib import Path
import hashlib
vals=[]
for raw in Path('.env').read_text(encoding='utf-8', errors='replace').splitlines():
    if raw.lstrip().startswith('ZFT_CONFIG_SECRET='):
        v=raw.split('=',1)[1].strip()
        if len(v)>=2 and v[0]==v[-1] and v[0] in {'"', "'"}: v=v[1:-1].strip()
        vals.append(v)
v=vals[-1] if vals else ''
print(f"{len(v)}:{hashlib.sha256(v.encode()).hexdigest() if v else '-'}")
PYHOSTMETA
)"
RUNNING_SECRET_META="$(docker compose exec -T "$SERVICE" python -c 'import os,hashlib; s=os.getenv("ZFT_CONFIG_SECRET","").strip(); print(str(len(s))+":"+(hashlib.sha256(s.encode()).hexdigest() if s else "-"))' | tr -d '\r\n')"
if [[ "$HOST_SECRET_META" != "$RUNNING_SECRET_META" ]]; then
  echo "ERROR: ZFT_CONFIG_SECRET inside the running container does not match .env." >&2
  echo "host/container fingerprints differ; the secret itself was not printed." >&2
  exit 1
fi
HOST_SECRET_LEN="${HOST_SECRET_META%%:*}"
if (( HOST_SECRET_LEN < 32 )); then
  echo "ERROR: effective ZFT_CONFIG_SECRET is shorter than 32 characters after trimming." >&2
  exit 1
fi
echo "Provider encryption secret verified: present, length=${HOST_SECRET_LEN}, fingerprint matched."

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
env=$ENV_HASH
EOFSTATE

echo "Health: $(cat /tmp/zft-health.json)"
echo "Update complete. If a browser tab was already open, reload it once to fetch the new index bundle."
