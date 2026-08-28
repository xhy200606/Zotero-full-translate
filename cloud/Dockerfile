FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    ZFT_STATIC_DIR=/app/static

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates fonts-noto-cjk \
    build-essential pkg-config libssl-dev cargo \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN ! grep -q '^cryptography==45\.0\.6$' requirements.txt \
    && python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -r requirements.txt \
    && python - <<'PY'
import cryptography
from packaging.version import Version
assert Version(cryptography.__version__) >= Version('46.0.7')
PY

COPY backend/app ./app
COPY --from=frontend-build /frontend/dist ./static
RUN mkdir -p /data/files /data/work

VOLUME ["/data"]
EXPOSE 8089
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8089", "--workers", "1"]
