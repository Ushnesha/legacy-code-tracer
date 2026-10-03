# The API server. It only reads data/<repo>.db and (for the Code tab) the repo under repos/, both mounted in.
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY extractor ./extractor
COPY server ./server
COPY scripts ./scripts
COPY repos-config ./repos-config
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
