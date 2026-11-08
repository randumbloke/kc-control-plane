"""Knowledge Catalog access: `lookupEntry` and whole-aspect writes (design §5.1, §5.4).

Writers never read their own aspect to merge with it: each write replaces the whole aspect
named in `aspectKeys` and nothing else (design §5.4, single-writer rule §3.2.1).
"""

from __future__ import annotations

from typing import Any, Protocol

from cdmc_common.transport import Transport

DATAPLEX = "https://dataplex.googleapis.com/v1"

SYSTEM_REFRESH_CADENCE = "dataplex-types.global.refresh-cadence"
SYSTEM_DQ_SCORECARD = "dataplex-types.global.data-quality-scorecard"
SYSTEM_CONTACTS = "dataplex-types.global.contacts"


def find_aspect(entry: dict[str, Any] | None, aspect_type: str) -> dict[str, Any] | None:
    """Return the `data` of an aspect by type name, matching the key suffix.

    Keys come back as `<project id or number>.<location>.<type>`, optionally with `@<path>`;
    only table-level (path-less) aspects are returned here.
    """
    if not entry:
        return None
    for key, aspect in (entry.get("aspects") or {}).items():
        if "@" in key:
            continue
        if key == aspect_type or key.endswith(f".{aspect_type}"):
            return aspect.get("data") or {}
    return None


class CatalogClient(Protocol):
    def lookup_entry(self, entry_name: str) -> dict[str, Any] | None:
        """Current entry with all aspects, or None when it does not exist."""
        ...

    def write_aspect(self, entry_name: str, aspect_key: str, data: dict[str, Any]) -> None:
        """Replace one whole aspect on an entry."""
        ...


class RestCatalogClient:
    def __init__(self, transport: Transport, attribution_project: str, location: str):
        self._t = transport
        self._parent = f"projects/{attribution_project}/locations/{location}"

    def lookup_entry(self, entry_name: str) -> dict[str, Any] | None:
        # The lookup is attributed to the hub; the entry's own location is in its name.
        location = entry_name.split("/locations/", 1)[1].split("/", 1)[0]
        project = self._parent.split("/")[1]
        url = f"{DATAPLEX}/projects/{project}/locations/{location}:lookupEntry"
        return self._t.get(url, params={"entry": entry_name, "view": "ALL"}, ok_statuses=(404,))

    def write_aspect(self, entry_name: str, aspect_key: str, data: dict[str, Any]) -> None:
        # entries.patch with updateMask=aspects and aspectKeys=[key] replaces exactly that
        # aspect. VERIFY IN BUILD (VA-76): ddaAspectWriter plus aspectTypes.use is enough.
        # VERIFY IN BUILD (VA-42): the evaluator cannot write system aspects.
        self._t.patch(
            f"{DATAPLEX}/{entry_name}",
            params={"updateMask": "aspects", "aspectKeys": aspect_key},
            json={"name": entry_name, "aspects": {aspect_key: {"data": data}}},
        )
