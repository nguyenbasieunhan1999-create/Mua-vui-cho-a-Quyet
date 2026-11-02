# ============================================================================
# STAGE 1: Build Frontend (React 18 + TypeScript + Vite)
# ============================================================================
FROM node:22-alpine AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ============================================================================
# STAGE 2: Unified Production Image (FastAPI + tlmn-engine + React SPA)
# ============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    STATIC_DIR=/app/static

COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY engine/pyproject.toml engine/pyproject.toml
COPY backend/pyproject.toml backend/pyproject.toml
COPY engine/src engine/src
COPY backend/src backend/src
RUN uv sync --locked --no-dev --package tlmn-backend

COPY backend/alembic.ini backend/alembic.ini
COPY backend/migrations backend/migrations
COPY --from=web-build /web/dist /app/static

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app
WORKDIR /app/backend
ENV PATH=/app/.venv/bin:$PATH
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --app-dir src --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
