"""Debounced product rollups through Cloud Tasks (design §2.5.2)."""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cdmc_common.transport import ApiError, Transport

TASKS = "https://cloudtasks.googleapis.com/v2"


def rollup_task_id(product_key: str, now: datetime, window_seconds: int) -> tuple[str, datetime]:
    """Task ID from the product and its time bucket, and the end of that bucket."""
    epoch = int(now.timestamp())
    bucket = epoch - epoch % window_seconds
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", product_key)
    end = datetime.fromtimestamp(bucket + window_seconds, tz=UTC)
    return f"rollup-{safe}-{bucket}", end


class RollupQueue(Protocol):
    def enqueue(self, product_key: str, now: datetime | None = None) -> str | None:
        """Create the rollup task for this product's time bucket; None when it exists."""
        ...


class CloudTasksRollupQueue:
    def __init__(
        self,
        transport: Transport,
        queue: str,
        handler_url: str,
        invoker_sa: str,
        window_seconds: int,
    ):
        self._t = transport
        self._queue = queue
        self._url = handler_url
        self._sa = invoker_sa
        self._window = window_seconds

    def enqueue(self, product_key: str, now: datetime | None = None) -> str | None:
        now = now or datetime.now(tz=UTC)
        task_id, end = rollup_task_id(product_key, now, self._window)
        name = f"{self._queue}/tasks/{task_id}"
        task = {
            "name": name,
            "scheduleTime": (end + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "httpRequest": {
                "httpMethod": "POST",
                "url": self._url,
                "headers": {"Content-Type": "application/json"},
                "body": base64.b64encode(json.dumps({"product": product_key}).encode()).decode(),
                "oidcToken": {"serviceAccountEmail": self._sa, "audience": self._url},
            },
        }
        try:
            self._t.post(f"{TASKS}/{self._queue}/tasks", json={"task": task})
        except ApiError as e:
            if e.status == 409:  # same product and bucket: already queued (debounce)
                return None
            raise
        return name
