"""Authenticated HTTP transport for Google REST APIs (google-auth + requests).

Tokens come from Application Default Credentials: the metadata server on Cloud Run, the
developer's ADC locally. No token is ever written to a file or a log.
"""

from __future__ import annotations

import time
from typing import Any

import google.auth
import requests
from google.auth.transport.requests import AuthorizedSession

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(self, status: int, method: str, url: str, body: str):
        super().__init__(f"{method} {url} -> {status}: {body[:500]}")
        self.status = status
        self.body = body


class Transport:
    """Thin JSON client with bounded exponential retry on transient errors."""

    def __init__(self, session: requests.Session | None = None, attempts: int = 5):
        if session is None:
            credentials, _ = google.auth.default(scopes=SCOPES)
            session = AuthorizedSession(credentials)
        self._session = session
        self._attempts = attempts

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        ok_statuses: tuple[int, ...] = (),
        raw: bool = False,
    ) -> Any:
        delay = 1.0
        for attempt in range(1, self._attempts + 1):
            resp = self._session.request(
                method, url, params=params, json=json, headers=headers, timeout=60
            )
            if resp.status_code < 300:
                if raw:
                    return resp.content
                return resp.json() if resp.content else {}
            if resp.status_code in ok_statuses:
                return None
            if resp.status_code in RETRY_STATUS and attempt < self._attempts:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise ApiError(resp.status_code, method, url, resp.text)
        raise AssertionError("unreachable")

    def get(self, url: str, **kw: Any) -> Any:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self.request("POST", url, **kw)

    def patch(self, url: str, **kw: Any) -> Any:
        return self.request("PATCH", url, **kw)

    def paged(self, url: str, items_key: str, params: dict[str, Any] | None = None) -> list[Any]:
        out: list[Any] = []
        p = dict(params or {})
        while True:
            body = self.get(url, params=p) or {}
            out.extend(body.get(items_key, []))
            token = body.get("nextPageToken")
            if not token:
                return out
            p["pageToken"] = token
