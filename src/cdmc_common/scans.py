"""Data quality scan inventory per data project (C12 coverage, design §3.6, §5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cdmc_common.names import TableRef
from cdmc_common.transport import Transport

DATAPLEX = "https://dataplex.googleapis.com/v1"


@dataclass(frozen=True)
class ScanInfo:
    name: str
    table: TableRef
    publishing_enabled: bool
    export_table: str | None


class ScanClient(Protocol):
    def list_quality_scans(self, project: str) -> list[ScanInfo]: ...


class RestScanClient:
    def __init__(self, transport: Transport, location: str):
        self._t = transport
        self._location = location

    def list_quality_scans(self, project: str) -> list[ScanInfo]:
        items = self._t.paged(
            f"{DATAPLEX}/projects/{project}/locations/{self._location}/dataScans",
            "dataScans",
            params={"view": "FULL"},
        )
        out = []
        for s in items:
            if s.get("type") != "DATA_QUALITY":
                continue
            resource = (s.get("data") or {}).get("resource", "")
            try:
                ref = TableRef.parse(resource)
            except ValueError:
                continue
            spec = s.get("dataQualitySpec") or {}
            export = ((spec.get("postScanActions") or {}).get("bigqueryExport") or {}).get(
                "resultsTable"
            )
            out.append(
                ScanInfo(s["name"], ref, bool(spec.get("catalogPublishingEnabled")), export or None)
            )
        return out
