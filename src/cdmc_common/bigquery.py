"""BigQuery access: dataset and table inventory, table metadata, result queries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from cdmc_common.names import TableRef
from cdmc_common.transport import Transport

BQ = "https://bigquery.googleapis.com/bigquery/v2"


@dataclass(frozen=True)
class DatasetInfo:
    project: str
    dataset: str
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def sensitive(self) -> bool:
        return self.labels.get("cdmc-sensitivity") == "sensitive"


@dataclass(frozen=True)
class TableInfo:
    ref: TableRef
    created: datetime
    last_modified: datetime
    table_type: str = "TABLE"


def _ms(value: str | int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


class BigQueryClient(Protocol):
    def list_datasets(self, project: str) -> list[DatasetInfo]: ...

    def list_tables(self, project: str, dataset: str) -> list[TableRef]: ...

    def get_dataset(self, project: str, dataset: str) -> DatasetInfo | None: ...

    def get_table(self, ref: TableRef) -> TableInfo | None: ...

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class RestBigQueryClient:
    def __init__(self, transport: Transport, job_project: str, location: str):
        self._t = transport
        self._job_project = job_project
        self._location = location

    def list_datasets(self, project: str) -> list[DatasetInfo]:
        items = self._t.paged(f"{BQ}/projects/{project}/datasets", "datasets")
        return [
            DatasetInfo(project, i["datasetReference"]["datasetId"], i.get("labels", {}))
            for i in items
        ]

    def get_dataset(self, project: str, dataset: str) -> DatasetInfo | None:
        body = self._t.get(f"{BQ}/projects/{project}/datasets/{dataset}", ok_statuses=(404,))
        return None if body is None else DatasetInfo(project, dataset, body.get("labels", {}))

    def list_tables(self, project: str, dataset: str) -> list[TableRef]:
        items = self._t.paged(f"{BQ}/projects/{project}/datasets/{dataset}/tables", "tables")
        return [TableRef(project, dataset, i["tableReference"]["tableId"]) for i in items]

    def get_table(self, ref: TableRef) -> TableInfo | None:
        body = self._t.get(
            f"{BQ}/projects/{ref.project}/datasets/{ref.dataset}/tables/{ref.table}",
            ok_statuses=(404,),
        )
        if body is None:
            return None
        return TableInfo(
            ref, _ms(body["creationTime"]), _ms(body["lastModifiedTime"]), body.get("type", "TABLE")
        )

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        qp = []
        for name, value in params.items():
            if isinstance(value, list):
                qp.append(
                    {
                        "name": name,
                        "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRING"}},
                        "parameterValue": {"arrayValues": [{"value": v} for v in value]},
                    }
                )
            else:
                qp.append(
                    {
                        "name": name,
                        "parameterType": {"type": "STRING"},
                        "parameterValue": {"value": str(value)},
                    }
                )
        body = self._t.post(
            f"{BQ}/projects/{self._job_project}/queries",
            json={
                "query": sql,
                "useLegacySql": False,
                "parameterMode": "NAMED",
                "queryParameters": qp,
                "location": self._location,
                "timeoutMs": 60000,
                "labels": {"dda-component": "evaluator"},
            },
        )
        fields = [f["name"] for f in body.get("schema", {}).get("fields", [])]
        rows = []
        for r in body.get("rows", []):
            rows.append({k: c.get("v") for k, c in zip(fields, r["f"], strict=True)})
        return rows
