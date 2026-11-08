"""Data Lineage access: upstream links with `searchLineageStreaming` (design §5.4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from cdmc_common.transport import Transport

DATALINEAGE = "https://datalineage.googleapis.com/v1"


@dataclass(frozen=True)
class LineageLink:
    source: str  # FQN
    target: str  # FQN
    depth: int


class LineageClient(Protocol):
    def search_upstream(self, fqn: str, max_depth: int) -> list[LineageLink]:
        """All asset-level links upstream of `fqn`, up to `max_depth` hops."""
        ...


class RestLineageClient:
    def __init__(self, transport: Transport, project: str, location: str):
        self._t = transport
        self._project = project
        self._location = location

    def search_upstream(self, fqn: str, max_depth: int) -> list[LineageLink]:
        url = (
            f"{DATALINEAGE}/projects/{self._project}/locations/{self._location}"
            ":searchLineageStreaming"
        )
        body = {
            "rootCriteria": {"entities": {"entities": [{"fullyQualifiedName": fqn}]}},
            "direction": "UPSTREAM",
            "locations": [self._location],
            "limits": {"maxDepth": min(max_depth, 100), "maxResults": 10000},
            "filters": {"entitySet": "ENTITIES"},
        }
        # VERIFY IN BUILD (VA-64): over REST the streaming method returns a JSON array of
        # response messages; a single object is accepted too. Also that a hub-parented search
        # returns links recorded in the data projects (no `filters.projects` set).
        resp = self._t.post(url, json=body)
        return parse_stream(resp)


def parse_stream(resp: Any) -> list[LineageLink]:
    messages = resp if isinstance(resp, list) else [resp or {}]
    links: list[LineageLink] = []
    for msg in messages:
        for link in msg.get("links", []) or []:
            src = (link.get("source") or {}).get("fullyQualifiedName")
            tgt = (link.get("target") or {}).get("fullyQualifiedName")
            if src and tgt:
                links.append(LineageLink(src, tgt, int(link.get("depth", 0) or 0)))
    return links
