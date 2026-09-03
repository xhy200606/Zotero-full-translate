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
SERVICE="zft"

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
  if [[ -f "$target" ]]; then sha256sum "$target" | awk '{print $1}'; return; fi
  find "$target" -type f ! -path '*/node_modules/*' ! -path '*/dist/*' ! -name '*.log' ! -name '*.pyc' -print0 | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | awk '{print $1}'
}
USER_FE_HASH="$(hash_tree user-frontend)"
ADMIN_FE_HASH="$(hash_tree admin-frontend)"

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
  ZFT_USER_FRONTEND_REV="$USER_FE_HASH" ZFT_ADMIN_FRONTEND_REV="$ADMIN_FE_HASH" DOCKER_BUILDKIT=1 docker compose build --no-cache --progress=plain zft
else
  echo "Cached rebuild: no prune, no --no-cache, no forced base-image pull."
  ZFT_USER_FRONTEND_REV="$USER_FE_HASH" ZFT_ADMIN_FRONTEND_REV="$ADMIN_FE_HASH" DOCKER_BUILDKIT=1 docker compose build --progress=plain zft
fi

ZFT_USER_FRONTEND_REV="$USER_FE_HASH" ZFT_ADMIN_FRONTEND_REV="$ADMIN_FE_HASH" docker compose up -d --force-recreate --remove-orphans zft

cat > .zft-runtime-state <<EOF
backend=$(hash_tree backend/app)
requirements=$(hash_tree backend/requirements.txt)
user_frontend=$(hash_tree user-frontend)
admin_frontend=$(hash_tree admin-frontend)
docker=$(cat Dockerfile docker-compose.yml | sha256sum | awk '{print $1}')
env=$(sha256sum .env | awk '{print $1}')
EOF

PORT="${ZFT_PORT:-}"
if [[ -z "$PORT" && -f .env ]]; then PORT="$(grep -E '^ZFT_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '\r' || true)"; fi
PORT="${PORT:-3005}"
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/tmp/zft-health.json 2>/dev/null; then
    ADMIN_PORT="${ZFT_ADMIN_PORT:-}"
    if [[ -z "$ADMIN_PORT" && -f .env ]]; then ADMIN_PORT="$(grep -E '^ZFT_ADMIN_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '\r' || true)"; fi
    ADMIN_PORT="${ADMIN_PORT:-3006}"
    if ! curl -fsS "http://127.0.0.1:${ADMIN_PORT}/health" >/tmp/zft-admin-health.json 2>/dev/null; then
      echo "ERROR: admin port ${ADMIN_PORT} is not reachable." >&2
      exit 1
    fi
    SERVED_ADMIN_REV="$(curl -fsS "http://127.0.0.1:${ADMIN_PORT}/build-id.txt" 2>/dev/null | tr -d '\r\n' || true)"
    if [[ "$SERVED_ADMIN_REV" != "$ADMIN_FE_HASH" ]]; then
      echo "ERROR: admin port ${ADMIN_PORT} is not serving the current admin frontend." >&2
      exit 1
    fi
    echo "Admin port verified: 0.0.0.0:${ADMIN_PORT} -> admin frontend ${ADMIN_FE_HASH:0:12}."
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
    RUNNING_SECRET_META="$(docker compose exec -T zft python -c 'import os,hashlib; s=os.getenv("ZFT_CONFIG_SECRET","").strip(); print(str(len(s))+":"+(hashlib.sha256(s.encode()).hexdigest() if s else "-"))' | tr -d '\r\n')"
    if [[ "$HOST_SECRET_META" != "$RUNNING_SECRET_META" ]]; then
      echo "ERROR: ZFT_CONFIG_SECRET inside the running container does not match .env." >&2
      exit 1
    fi
    HOST_SECRET_LEN="${HOST_SECRET_META%%:*}"
    (( HOST_SECRET_LEN >= 32 )) || { echo "ERROR: effective ZFT_CONFIG_SECRET is shorter than 32 characters after trimming." >&2; exit 1; }
    echo "Provider encryption secret verified: present, length=${HOST_SECRET_LEN}, fingerprint matched."
    verify_config_secret
    echo "Health: $(cat /tmp/zft-health.json)"
    echo "Rebuild complete."
    exit 0
  fi
  sleep 2
done
echo "ERROR: health check failed" >&2
docker compose logs --tail=120 zft >&2 || true
exit 1
