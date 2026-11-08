"""HTTP entrypoints of the control evaluator Cloud Run service (design §2.5, §5.1).

- POST /events/kc     Pub/Sub push from `kc-evaluator` (`feed-evaluator` metadata feed)
- POST /events/state  Eventarc audit event: object created under `products/` or `lineage/`
- POST /tasks/rollup  Cloud Tasks `product-rollups` (debounced C12 product rollup)
- GET  /healthz

Each handler acknowledges (2xx) only after it has written its result; an error returns 500
so Pub/Sub, Eventarc or Cloud Tasks retries (design §2.5.1). Authentication is by Cloud Run
IAM (OIDC tokens of the push and task identities); the service is not public.
"""

from __future__ import annotations

import json
import logging
import os

from flask import Flask, jsonify, request

from evaluator.deps import Deps, build_deps
from evaluator.handlers import handle_feed_event, handle_rollup_task, handle_state_event

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format='{"severity":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
log = logging.getLogger("evaluator.service")


def create_app(deps: Deps | None = None) -> Flask:
    app = Flask(__name__)
    holder: dict[str, Deps] = {}

    def get_deps() -> Deps:
        if "deps" not in holder:
            holder["deps"] = deps or build_deps()
        return holder["deps"].fresh()

    def run(fn, *args):
        try:
            result = fn(get_deps(), *args)
        except Exception:  # retried by the caller; dead-lettered after 10 attempts
            log.exception("handler failed")
            return jsonify({"error": "handler failed"}), 500
        log.info(json.dumps({"actions": result.actions, "findings": result.findings}))
        return jsonify({"actions": result.actions, "findings": result.findings}), 200

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.post("/events/kc")
    def events_kc():
        return run(handle_feed_event, request.get_json(force=True, silent=True) or {})

    @app.post("/events/state")
    def events_state():
        body = request.get_json(force=True, silent=True) or {}
        headers = {k.lower(): v for k, v in request.headers.items()}
        return run(handle_state_event, body, headers)

    @app.post("/tasks/rollup")
    def tasks_rollup():
        body = request.get_json(force=True, silent=True) or {}
        task = request.headers.get("X-CloudTasks-TaskName", "")
        return run(handle_rollup_task, body, task)

    return app


def app_factory() -> Flask:  # gunicorn "evaluator.service:app_factory()"
    return create_app()
