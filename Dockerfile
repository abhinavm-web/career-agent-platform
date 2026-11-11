FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[web,jobspy,ai,resume,lca]' \
    && useradd --create-home --uid 10001 career \
    && mkdir -p /app/.local \
    && chown career:career /app/.local

USER career
ENTRYPOINT ["career-agent"]
CMD ["serve"]
