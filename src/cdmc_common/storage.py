"""Hub state bucket access (design §2.3.2, §3.2.1, §5.4.1)."""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.parse import quote

from cdmc_common.transport import Transport

GCS = "https://storage.googleapis.com/storage/v1"


class StateStore(Protocol):
    def read_json(self, path: str) -> Any | None:
        """Object content parsed as JSON, or None when it does not exist."""
        ...

    def list(self, prefix: str) -> list[str]:
        """Object paths under a prefix."""
        ...


class GcsStateStore:
    def __init__(self, transport: Transport, bucket: str):
        self._t = transport
        self.bucket = bucket

    def read_json(self, path: str) -> Any | None:
        content = self._t.get(
            f"{GCS}/b/{self.bucket}/o/{quote(path, safe='')}",
            params={"alt": "media"},
            ok_statuses=(404,),
            raw=True,
        )
        return None if content is None else json.loads(content)

    def list(self, prefix: str) -> list[str]:
        items = self._t.paged(f"{GCS}/b/{self.bucket}/o", "items", params={"prefix": prefix})
        return [i["name"] for i in items]
