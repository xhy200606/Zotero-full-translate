# syntax=docker/dockerfile:1.7

FROM node:20-alpine AS user-frontend-build
WORKDIR /frontend
COPY user-frontend/package.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund
COPY user-frontend/ ./
ARG VITE_API_BASE_URL=""
ARG USER_FRONTEND_REV="dev"
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN printf '%s\n' "$USER_FRONTEND_REV" > /tmp/frontend-rev \
    && npm run build \
    && printf '%s\n' "$USER_FRONTEND_REV" > /frontend/dist/build-id.txt

FROM node:20-alpine AS admin-frontend-build
WORKDIR /frontend
COPY admin-frontend/package.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund
COPY admin-frontend/ ./
ARG VITE_ADMIN_API_BASE_URL=""
ARG ADMIN_FRONTEND_REV="dev"
ENV VITE_API_BASE_URL=${VITE_ADMIN_API_BASE_URL}
RUN printf '%s\n' "$ADMIN_FRONTEND_REV" > /tmp/frontend-rev \
    && npm run build \
    && printf '%s\n' "$ADMIN_FRONTEND_REV" > /frontend/dist/build-id.txt

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ZFT_STATIC_DIR=/app/static/user \
    ZFT_ADMIN_STATIC_DIR=/app/static/admin

RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates fonts-noto-cjk \
    build-essential pkg-config libssl-dev cargo \
    libgl1 libglib2.0-0 libgomp1

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    ! grep -q '^cryptography==45\.0\.6$' requirements.txt \
    && python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -r requirements.txt \
    && python - <<'PY'
import cryptography
from packaging.version import Version
assert Version(cryptography.__version__) >= Version('46.0.7')
PY

COPY backend/app ./app
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY --from=user-frontend-build /frontend/dist ./static/user
COPY --from=admin-frontend-build /frontend/dist ./static/admin
RUN mkdir -p /data/files /data/work

VOLUME ["/data"]
EXPOSE 8089
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8089", "--workers", "1"]
