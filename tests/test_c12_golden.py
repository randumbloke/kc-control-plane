"""Golden fixtures for every C12 rule (ref arch §9.5, §9.7; design §5.4; AT-14 to AT-16)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cdmc_common import aspects
from cdmc_common.catalog import SYSTEM_REFRESH_CADENCE
from cdmc_common.names import TableRef
from cdmc_common.products import PublishedProduct
from evaluator.c12.module import QUALITY_ASPECT, ROLLUP_VERSION, C12Module
from tests import estate
from tests.fakes import FakeStore, make_deps

GOLDEN = sorted((Path(__file__).parent / "golden" / "c12").glob("*.json"))
HUB = estate.HUB


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build(fx: dict):
    now = _ts(fx["now"])
    assets = {}
    for t in fx["tables"]:
        ref = TableRef.parse(t["ref"])
        assets[ref.table] = {"table_resource": ref.resource, "form": "pipeline_table"}
    product_data = estate.published_customer_profile(assets=assets)
    path = "products/dev/customer-dev/customer-profile.json"
    deps = make_deps(now, store=FakeStore({path: product_data}))
    aspects_map = {}
    if fx.get("cadence") is not None:
        aspects_map[SYSTEM_REFRESH_CADENCE] = {"data": fx["cadence"]}
    if fx.get("contract") is not None:
        aspects_map[f"{HUB}.global.cdmc-data-product-contract"] = {"data": fx["contract"]}
    deps.catalog.entries[estate.CUSTOMER_PROFILE_ENTRY] = {
        "name": estate.CUSTOMER_PROFILE_ENTRY,
        "aspects": aspects_map,
    }
    seen_ds = set()
    for t in fx["tables"]:
        ref = TableRef.parse(t["ref"])
        if (ref.project, ref.dataset, t["sensitive"]) not in seen_ds:
            deps.bigquery.add_dataset(ref.project, ref.dataset, t["sensitive"])
            seen_ds.add((ref.project, ref.dataset, t["sensitive"]))
        deps.bigquery.add_table(
            t["ref"], now - timedelta(days=30), now - timedelta(hours=t["modified_hours_ago"])
        )
        scan = t["scan"]
        if scan.get("exists"):
            deps.scans.add(
                t["ref"],
                publishing=scan.get("publishing", True),
                export="default" if scan.get("export", True) else None,
            )
        if t.get("job"):
            end = now - timedelta(hours=t["job"]["end_hours_ago"])
            for name, passed, critical in t["job"]["rules"]:
                deps.bigquery.result_rows.append(
                    {
                        "table_ref": ref.dotted,
                        "job_id": f"{ref.table}-job-latest",
                        "job_end_time": end.isoformat(),
                        "rule_name": name,
                        "rule_passed": str(passed).lower(),
                        "critical": "true" if critical else None,
                    }
                )
            # An older job for the same table must be ignored (latest job per table).
            older = now - timedelta(hours=t["job"]["end_hours_ago"] + 24)
            deps.bigquery.result_rows.append(
                {
                    "table_ref": ref.dotted,
                    "job_id": f"{ref.table}-job-older",
                    "job_end_time": older.isoformat(),
                    "rule_name": "old",
                    "rule_passed": "false",
                    "critical": "true",
                }
            )
    return deps, PublishedProduct(path, "customer-dev", "customer-profile", product_data)


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_c12_golden(path: Path):
    fx = json.loads(path.read_text())
    exp = fx["expected"]
    deps, product = build(fx)
    findings = C12Module(deps).rollup(product, "EVENT", "task-1")

    status = next(f for f in findings if f.check == "C12.product_status")
    assert status.evaluation.detail["control_status"] == exp["status"]
    assert status.evaluation.status == ("PASS" if exp["status"] == "COMPLIANT" else "FAIL")
    assert status.evaluation.measured_value == exp["score"]
    assert status.asset_type == "DATA_PRODUCT" and status.narrative is None
    fresh = [f for f in findings if f.check == "C12.freshness"]
    if exp["freshness"] is None:
        assert fresh == []
    else:
        assert fresh[0].evaluation.detail["control_status"] == exp["freshness"]
        assert fresh[0].evaluation.status == ("PASS" if exp["freshness"] == "ON_TIME" else "FAIL")

    ((entry, key, data),) = deps.catalog.writes
    assert entry == estate.CUSTOMER_PROFILE_ENTRY
    assert key == f"{HUB}.global.{QUALITY_ASPECT}"
    aspects.validate(QUALITY_ASPECT, data)
    assert data["aggregate_dq_status"] == exp["status"]
    assert data.get("aggregate_dq_score") == exp["score"]
    assert data.get("freshness_status") == exp["freshness"]
    assert data["evaluation"]["evaluator_version"] == ROLLUP_VERSION
    assert data["schema_version"] == 1


def test_product_without_dataplex_entry_is_not_evaluated():
    fx = json.loads(GOLDEN[0].read_text())
    deps, product = build(fx)
    product.data["dataplex_entry"] = None
    (f,) = C12Module(deps).rollup(product, "SWEEP", "sweep-1")
    assert f.evaluation.status == "NOT_EVALUATED"
    assert deps.catalog.writes == []


def test_rollup_queries_only_product_tables():
    fx = json.loads(GOLDEN[2].read_text())
    deps, product = build(fx)
    C12Module(deps).rollup(product, "EVENT", "t")
    ((_, params),) = deps.bigquery.queries
    assert sorted(params["tables"]) == sorted(TableRef.parse(t["ref"]).dotted for t in fx["tables"])
