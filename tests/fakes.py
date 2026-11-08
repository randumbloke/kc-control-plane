"""In-memory fakes of the cdmc_common interfaces."""

from __future__ import annotations

import copy
from collections import deque
from datetime import datetime
from typing import Any

from cdmc_common.bigquery import DatasetInfo, TableInfo
from cdmc_common.config import Settings
from cdmc_common.findings import Finding
from cdmc_common.lineage import LineageLink
from cdmc_common.names import TableRef
from cdmc_common.scans import ScanInfo
from cdmc_common.tasks import rollup_task_id
from evaluator.deps import Deps


class FakeCatalog:
    def __init__(self, entries: dict[str, dict] | None = None):
        self.entries = entries or {}
        self.writes: list[tuple[str, str, dict]] = []
        self.missing: set[str] = set()  # BigQuery entries discovery has not created

    def lookup_entry(self, entry_name: str) -> dict | None:
        if entry_name in self.missing:
            return None
        if "/entryGroups/@bigquery/" in entry_name and entry_name not in self.entries:
            return {"name": entry_name, "aspects": {}}
        e = self.entries.get(entry_name)
        return copy.deepcopy(e) if e is not None else None

    def write_aspect(self, entry_name: str, aspect_key: str, data: dict) -> None:
        self.writes.append((entry_name, aspect_key, copy.deepcopy(data)))
        entry = self.entries.setdefault(entry_name, {"name": entry_name, "aspects": {}})
        entry.setdefault("aspects", {})[aspect_key] = {"data": copy.deepcopy(data)}


class FakeLineage:
    """Simulates the server-side upstream traversal of searchLineageStreaming."""

    def __init__(self, edges: list[tuple[str, str]] | None = None):
        self.edges = edges or []
        self.calls: list[tuple[str, int]] = []

    def search_upstream(self, fqn: str, max_depth: int) -> list[LineageLink]:
        self.calls.append((fqn, max_depth))
        up: dict[str, list[str]] = {}
        for src, tgt in self.edges:
            up.setdefault(tgt, []).append(src)
        out, seen = [], set()
        q = deque([(fqn, 0)])
        while q:
            node, depth = q.popleft()
            if depth >= max_depth:
                continue
            for src in up.get(node, []):
                if (src, node) in seen:
                    continue
                seen.add((src, node))
                out.append(LineageLink(src, node, depth + 1))
                q.append((src, depth + 1))
        return out


class FakeDomains:
    def __init__(self, domains: list[dict] | None = None, bindings: dict[str, list] | None = None):
        self.domains = domains or []
        self.bindings = bindings or {}

    def list_domains(self) -> list[dict]:
        return self.domains

    def list_bindings(self, domain_name: str) -> list[dict]:
        return self.bindings.get(domain_name, [])


class FakeStore:
    def __init__(self, objects: dict[str, Any] | None = None):
        self.objects = objects or {}

    def read_json(self, path: str) -> Any | None:
        return copy.deepcopy(self.objects.get(path))

    def list(self, prefix: str) -> list[str]:
        return sorted(p for p in self.objects if p.startswith(prefix))


class FakeBigQuery:
    def __init__(self):
        self.datasets: dict[str, list[DatasetInfo]] = {}
        self.tables: dict[TableRef, TableInfo] = {}
        self.result_rows: list[dict] = []
        self.queries: list[tuple[str, dict]] = []

    def add_dataset(self, project: str, dataset: str, sensitive: bool) -> None:
        labels = {"cdmc-sensitivity": "sensitive" if sensitive else "non-sensitive"}
        self.datasets.setdefault(project, []).append(DatasetInfo(project, dataset, labels))

    def add_table(self, dotted: str, created: datetime, modified: datetime | None = None):
        ref = TableRef.parse(dotted)
        self.tables[ref] = TableInfo(ref, created, modified or created)
        return ref

    def list_datasets(self, project: str) -> list[DatasetInfo]:
        return self.datasets.get(project, [])

    def get_dataset(self, project: str, dataset: str) -> DatasetInfo | None:
        for d in self.datasets.get(project, []):
            if d.dataset == dataset:
                return d
        return None

    def list_tables(self, project: str, dataset: str) -> list[TableRef]:
        return sorted(r for r in self.tables if r.project == project and r.dataset == dataset)

    def get_table(self, ref: TableRef) -> TableInfo | None:
        return self.tables.get(ref)

    def query(self, sql: str, params: dict) -> list[dict]:
        self.queries.append((sql, params))
        wanted = set(params.get("tables", []))
        return [r for r in self.result_rows if r["table_ref"] in wanted]


class FakeScans:
    def __init__(self):
        self.scans: dict[str, list[ScanInfo]] = {}

    def add(self, dotted: str, publishing: bool = True, export: str | None = "default"):
        ref = TableRef.parse(dotted)
        if export == "default":
            export = "//bigquery.googleapis.com/projects/kcdda-dgov-hub-nonprod/datasets/dq_results/tables/scan_results"
        name = f"projects/{ref.project}/locations/australia-southeast1/dataScans/dq-{ref.dataset}-{ref.table}"
        self.scans.setdefault(ref.project, []).append(ScanInfo(name, ref, publishing, export))

    def list_quality_scans(self, project: str) -> list[ScanInfo]:
        return self.scans.get(project, [])


class FakeQueue:
    def __init__(self, window: int = 300):
        self.window = window
        self.tasks: list[str] = []

    def enqueue(self, product_key: str, now: datetime | None = None) -> str | None:
        assert now is not None
        task_id, _ = rollup_task_id(product_key, now, self.window)
        if task_id in self.tasks:
            return None
        self.tasks.append(task_id)
        return task_id


class FakePublisher:
    def __init__(self):
        self.findings: list[Finding] = []

    def publish(self, finding: Finding) -> None:
        self.findings.append(finding)

    def by_check(self, check: str) -> list[Finding]:
        return [f for f in self.findings if f.check == check]


def make_deps(now: datetime, **overrides: Any) -> Deps:
    d = Deps(
        settings=overrides.pop("settings", Settings()),
        catalog=FakeCatalog(),
        lineage=FakeLineage(),
        domains=FakeDomains(),
        store=FakeStore(),
        bigquery=FakeBigQuery(),
        scans=FakeScans(),
        queue=FakeQueue(),
        publisher=FakePublisher(),
        clock=lambda: now,
    )
    for k, v in overrides.items():
        setattr(d, k, v)
    return d
