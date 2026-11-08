# Control evaluator images (design §5.1, §6.10). One Dockerfile, two targets, two images:
#   --target service  -> dda-control-plane/evaluator        (Cloud Run service)
#   --target job      -> dda-control-plane/evaluator-sweep  (Cloud Run job, daily sweep)
# Build context: the repository root.
# VERIFY IN BUILD (estate): pin both base images by digest once the registry mirror is known.
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

FROM base AS service
# Concurrency 10, 300 s timeout (design §5.1); Cloud Run sets $PORT.
ENV PORT=8080
CMD exec gunicorn --bind ":${PORT}" --workers 1 --threads 10 --timeout 300 \
    "evaluator.service:app_factory()"

FROM base AS job
# Task i of CLOUD_RUN_TASK_COUNT (20 in design §5.1) takes its shard of projects and products.
CMD ["evaluator-sweep"]
