FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8003

COPY pyproject.toml alembic.ini entrypoint.sh ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
COPY tests ./tests

RUN pip install --no-cache-dir ".[test]" && chmod +x /app/entrypoint.sh

EXPOSE 8003

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8003}"]
