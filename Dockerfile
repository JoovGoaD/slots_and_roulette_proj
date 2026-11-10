FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra server

FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --gid 10001 casino \
    && useradd --uid 10001 --gid casino --no-create-home casino \
    && install -d -m 0750 -o casino -g casino /var/lib/casino \
    && install -m 0640 -o casino -g casino /dev/null /var/lib/casino/.volume
COPY --from=build /app/.venv /app/.venv
COPY casino/ ./casino/
USER casino
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "casino.backend.server:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "100"]
