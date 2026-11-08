"""REST adapters: request shapes against the Dataplex, Data Lineage, Pub/Sub, Cloud Tasks,
BigQuery and Cloud Storage APIs, with a recording session (no network)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest

from cdmc_common.bigquery import RestBigQueryClient
from cdmc_common.catalog import RestCatalogClient, find_aspect
from cdmc_common.domains import RestDomainsAdapter
from cdmc_common.findings import PubSubFindingsPublisher, make_finding
from cdmc_common.lineage import RestLineageClient
from cdmc_common.names import TableRef
from cdmc_common.scans import RestScanClient
from cdmc_common.storage import GcsStateStore
from cdmc_common.tasks import CloudTasksRollupQueue
from cdmc_common.transport import ApiError, Transport


class Resp:
    def __init__(self, status=200, body=None, raw=None):
        self.status_code = status
        self._body = body
        self.content = (
            raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        )
        self.text = self.content.decode()

    def json(self):
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0) if self.responses else Resp(200, {})


def t(*responses):
    s = Session(*responses)
    return Transport(session=s, attempts=2), s


def test_lookup_entry_and_whole_aspect_write():
    tr, s = t(Resp(200, {"aspects": {"123.global.cdmc-data-product-contract": {"data": {"x": 1}}}}))
    c = RestCatalogClient(tr, "hub", "australia-southeast1")
    entry = "projects/p/locations/australia-southeast1/entryGroups/@dataplex/entries/e"
    got = c.lookup_entry(entry)
    assert find_aspect(got, "cdmc-data-product-contract") == {"x": 1}
    m, url, kw = s.calls[0]
    assert m == "GET" and url.endswith("/projects/hub/locations/australia-southeast1:lookupEntry")
    assert kw["params"] == {"entry": entry, "view": "ALL"}
    c.write_aspect(entry, "hub.global.cdmc-data-product-contract", {"a": 1})
    m, url, kw = s.calls[1]
    assert m == "PATCH" and url.endswith(entry)
    assert kw["params"] == {
        "updateMask": "aspects",
        "aspectKeys": "hub.global.cdmc-data-product-contract",
    }
    assert kw["json"]["aspects"] == {"hub.global.cdmc-data-product-contract": {"data": {"a": 1}}}


def test_lookup_missing_entry_is_none():
    tr, _ = t(Resp(404, {"error": {}}))
    assert (
        RestCatalogClient(tr, "hub", "l").lookup_entry(
            "projects/p/locations/l/entryGroups/g/entries/e"
        )
        is None
    )


def test_lineage_request_body():
    link = {
        "source": {"fullyQualifiedName": "gcs:b.x"},
        "target": {"fullyQualifiedName": "bigquery:p.d.t"},
        "depth": 1,
    }
    tr, s = t(Resp(200, [{"links": [link]}]))
    links = RestLineageClient(tr, "hub", "australia-southeast1").search_upstream(
        "bigquery:p.d.t", 21
    )
    assert links[0].source == "gcs:b.x"
    _, url, kw = s.calls[0]
    assert url.endswith(":searchLineageStreaming")
    assert kw["json"]["direction"] == "UPSTREAM"
    assert kw["json"]["limits"]["maxDepth"] == 21
    assert kw["json"]["rootCriteria"]["entities"]["entities"] == [
        {"fullyQualifiedName": "bigquery:p.d.t"}
    ]


def test_domains_adapter_pages():
    tr, s = t(
        Resp(200, {"dataDomains": [{"name": "a"}], "nextPageToken": "n"}),
        Resp(200, {"dataDomains": [{"name": "b"}]}),
        Resp(200, {"dataDomainBindings": [{"name": "x", "resource": "//r"}]}),
    )
    a = RestDomainsAdapter(tr, "hub", "australia-southeast1")
    assert [d["name"] for d in a.list_domains()] == ["a", "b"]
    assert s.calls[1][2]["params"] == {"pageToken": "n"}
    assert a.list_bindings("projects/hub/locations/l/dataDomains/a")[0]["resource"] == "//r"
    assert s.calls[2][1].endswith("/dataDomains/a/bindings")


def test_publisher_encodes_message():
    tr, s = t(Resp(200, {"messageIds": ["1"]}))
    f = make_finding(
        env="dev",
        control="C12",
        check="C12.coverage",
        asset="//t",
        asset_type="TABLE",
        status="FAIL",
        evaluator_version="v",
        trigger="SWEEP",
        trigger_id="x",
        source_ref="r",
        now=datetime(2026, 11, 9, tzinfo=UTC),
    )
    PubSubFindingsPublisher(tr, "projects/hub/topics/findings").publish(f)
    _, url, kw = s.calls[0]
    assert url.endswith("/projects/hub/topics/findings:publish")
    msg = kw["json"]["messages"][0]
    assert json.loads(base64.b64decode(msg["data"]))["check"] == "C12.coverage"
    assert msg["attributes"]["status"] == "FAIL"


def test_cloud_tasks_debounce_on_409():
    tr, s = t(Resp(200, {"name": "x"}), Resp(409, {"error": {"status": "ALREADY_EXISTS"}}))
    q = CloudTasksRollupQueue(
        tr,
        "projects/h/locations/l/queues/product-rollups",
        "https://svc/tasks/rollup",
        "evaluator-dev@h.iam.gserviceaccount.com",
        300,
    )
    now = datetime(2026, 11, 9, 0, 2, tzinfo=UTC)
    assert q.enqueue("dev/customer-dev/customer-profile", now)
    assert q.enqueue("dev/customer-dev/customer-profile", now) is None
    task = s.calls[0][2]["json"]["task"]
    assert task["scheduleTime"] == "2026-11-09T00:05:01Z"
    assert task["httpRequest"]["oidcToken"]["audience"] == "https://svc/tasks/rollup"


def test_transport_retries_then_raises():
    tr, s = t(Resp(503, {}), Resp(500, {}))
    with pytest.raises(ApiError):
        tr.get("https://x")
    assert len(s.calls) == 2


def test_bigquery_and_storage_and_scans():
    tr, s = t(
        Resp(
            200,
            {"creationTime": "1762646400000", "lastModifiedTime": "1762650000000", "type": "TABLE"},
        ),
        Resp(200, {"schema": {"fields": [{"name": "a"}]}, "rows": [{"f": [{"v": "1"}]}]}),
        Resp(200, raw=b'{"k": 1}'),
        Resp(
            200,
            {
                "dataScans": [
                    {
                        "name": "s",
                        "type": "DATA_QUALITY",
                        "data": {
                            "resource": "//bigquery.googleapis.com/projects/p/datasets/d/tables/t"
                        },
                        "dataQualitySpec": {"catalogPublishingEnabled": True},
                    }
                ]
            },
        ),
    )
    bq = RestBigQueryClient(tr, "hub", "australia-southeast1")
    info = bq.get_table(TableRef("p", "d", "t"))
    assert info.created.year == 2025 or info.created.year == 2026
    assert bq.query("SELECT 1", {"tables": ["p.d.t"]}) == [{"a": "1"}]
    q = s.calls[1][2]["json"]
    assert q["queryParameters"][0]["parameterType"]["type"] == "ARRAY"
    assert GcsStateStore(tr, "b").read_json("products/dev/x.json") == {"k": 1}
    assert "products%2Fdev%2Fx.json" in s.calls[2][1]
    (scan,) = RestScanClient(tr, "australia-southeast1").list_quality_scans("p")
    assert scan.publishing_enabled and scan.export_table is None
