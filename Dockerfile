FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && pip install --no-cache-dir '.[web,jobspy,ai,resume,lca,pdf,backup]' \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 career \
    && mkdir -p /app/.local \
    && chown career:career /app/.local

USER career
ENTRYPOINT ["career-agent"]
CMD ["serve"]
