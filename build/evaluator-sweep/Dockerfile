# Control evaluator daily sweep job image -> dda-control-plane/evaluator-sweep (design §5.1, §6.10).
# Build context: repository root.
FROM ghcr.io/astral-sh/uv:0.12.16 AS uv

FROM python:3.13-slim AS base
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY schemas ./schemas
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && rm -rf /root/.cache
ENV PATH=/app/.venv/bin:$PATH
USER 65532:65532

FROM base AS job
# Task i of CLOUD_RUN_TASK_COUNT (20 in design §5.1) takes its shard of projects and products.
CMD ["evaluator-sweep"]
