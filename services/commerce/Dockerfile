# Build context is the repository root (the shared observability library lives in libs/).
FROM python:3.12-slim

WORKDIR /srv
COPY libs/observability libs/observability
COPY services/commerce services/commerce
RUN pip install --no-cache-dir libs/observability services/commerce \
    && useradd --system --no-create-home app

USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
