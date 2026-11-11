# Build context is the repository root: the uv workspace lock and shared libs live there.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/srv/.venv/bin:$PATH"

WORKDIR /srv
COPY pyproject.toml uv.lock ./
COPY libs libs
COPY services services
RUN uv sync --frozen --no-dev --no-editable --package expert-consultation \
    && useradd --system --no-create-home app

WORKDIR /srv/services/expert-consultation
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000"]
