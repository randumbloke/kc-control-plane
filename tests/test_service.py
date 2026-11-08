"""HTTP entrypoints, feed handling, debounce (AT-11, AT-12) and the state bucket trigger."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

from evaluator.service import create_app
from tests import estate
from tests.fakes import FakeDomains, FakeStore, make_deps

NOW = datetime(2026, 11, 9, 0, 1, tzinfo=UTC)
PROFILE = f"{estate.CUST_AN}.customer_analytics.customer_profile"
PRODUCT_PATH = "products/dev/customer-dev/customer-profile.json"


def deps():
    domains, bindings = estate.domains_and_bindings()
    d = make_deps(
        NOW,
        store=FakeStore({PRODUCT_PATH: estate.published_customer_profile()}),
        domains=FakeDomains(domains, bindings),
    )
    d.bigquery.add_dataset(estate.CUST_AN, "customer_analytics", True)
    d.bigquery.add_table(PROFILE, NOW - timedelta(days=3))
    return d


def push(entry: str, message_id: str = "m1") -> dict:
    payload = {"entry": {"name": entry}, "changeType": "UPDATE"}  # VA-09: shape unverified
    return {
        "message": {
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
            "messageId": message_id,
        },
        "subscription": "s",
    }


def table_entry(dotted: str) -> str:
    p, ds, t = dotted.split(".")
    return (
        f"projects/{p}/locations/{estate.LOC}/entryGroups/@bigquery/entries/"
        f"bigquery.googleapis.com/projects/{p}/datasets/{ds}/tables/{t}"
    )


def test_healthz():
    client = create_app(deps()).test_client()
    assert client.get("/healthz").json == {"status": "ok"}


def test_feed_event_evaluates_table_and_enqueues_rollup():
    d = deps()
    client = create_app(d).test_client()
    r = client.post("/events/kc", json=push(table_entry(PROFILE)))
    assert r.status_code == 200
    checks = sorted(f.check for f in d.publisher.findings)
    assert checks == ["C12.coverage", "C14.lineage"]
    assert len(d.queue.tasks) == 1
    assert any(
        a.startswith("rollup-enqueued:dev/customer-dev/customer-profile") for a in r.json["actions"]
    )


def test_twenty_table_updates_produce_one_rollup_task():
    d = deps()
    client = create_app(d).test_client()
    for i in range(20):
        assert (
            client.post("/events/kc", json=push(table_entry(PROFILE), f"m{i}")).status_code == 200
        )
    assert len(d.queue.tasks) == 1


def test_product_entry_event_enqueues_rollup():
    d = deps()
    r = create_app(d).test_client().post("/events/kc", json=push(estate.CUSTOMER_PROFILE_ENTRY))
    assert r.status_code == 200 and len(d.queue.tasks) == 1


def test_event_without_entry_is_acknowledged():
    d = deps()
    r = create_app(d).test_client().post("/events/kc", json={"message": {"data": ""}})
    assert r.status_code == 200 and r.json["actions"] == ["ignored:no-entry"]


def test_handler_error_returns_500_for_retry():
    d = deps()

    def boom(*_a, **_k):
        raise RuntimeError("lineage unavailable")

    d.lineage.search_upstream = boom
    r = create_app(d).test_client().post("/events/kc", json=push(table_entry(PROFILE)))
    assert r.status_code == 500


def audit_event(path: str, uploader: str) -> dict:
    return {
        "protoPayload": {
            "methodName": "storage.objects.create",
            "resourceName": f"projects/_/buckets/{estate.BUCKET}/objects/{path}",
            "authenticationInfo": {"principalEmail": uploader},
        },
        "insertId": "i1",
    }


def test_state_event_runs_contract_writer():
    d = deps()
    r = (
        create_app(d)
        .test_client()
        .post(
            "/events/state",
            json=audit_event(PRODUCT_PATH, estate.DEPLOYER),
            headers={"ce-id": "ce-1"},
        )
    )
    assert r.status_code == 200 and r.json["actions"] == ["contract:written"]
    assert d.catalog.writes[0][1].endswith("cdmc-data-product-contract")


def test_state_event_rejects_wrong_uploader():
    d = deps()
    r = (
        create_app(d)
        .test_client()
        .post("/events/state", json=audit_event(PRODUCT_PATH, "mallory@example.com"))
    )
    assert r.status_code == 200 and d.catalog.writes == []
    assert d.publisher.findings[0].check == "C12.contract_source"


def test_manifest_upload_by_wrong_uploader_publishes_finding():
    d = deps()
    path = "lineage/dev/customer-dev/manifest.json"
    d.store.objects[path] = {
        "version": 1,
        "domain": "customer-dev",
        "targets": {PROFILE: {"sources": []}},
    }
    r = create_app(d).test_client().post("/events/state", json=audit_event(path, "x@y.com"))
    assert r.status_code == 200
    (f,) = d.publisher.findings
    assert f.check == "C14.manifest_source" and f.evaluation.status == "FAIL"


def test_manifest_upload_reevaluates_targets():
    d = deps()
    path = "lineage/dev/customer-dev/manifest.json"
    d.store.objects[path] = {
        "version": 1,
        "domain": "customer-dev",
        "targets": {PROFILE: {"sources": []}},
    }
    r = create_app(d).test_client().post("/events/state", json=audit_event(path, estate.DEPLOYER))
    assert r.status_code == 200
    assert [f.check for f in d.publisher.findings if f.control == "C14"] == ["C14.lineage"]


def test_rollup_task_endpoint():
    d = deps()
    d.catalog.entries[estate.CUSTOMER_PROFILE_ENTRY] = {"aspects": {}}
    r = (
        create_app(d)
        .test_client()
        .post(
            "/tasks/rollup",
            json={"product": "dev/customer-dev/customer-profile"},
            headers={"X-CloudTasks-TaskName": "rollup-x"},
        )
    )
    assert r.status_code == 200
    status = d.publisher.by_check("C12.product_status")[0]
    assert status.evaluation.detail["control_status"] == "NOT_EVALUATED"


def test_modules_follow_component_flags():
    import dataclasses

    from cdmc_common.config import Settings

    d = deps()
    d.settings = dataclasses.replace(Settings(), evaluator_modules=["c14"])
    create_app(d).test_client().post("/events/kc", json=push(table_entry(PROFILE)))
    assert [f.check for f in d.publisher.findings] == ["C14.lineage"]
    assert d.queue.tasks == []
