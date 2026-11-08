"""Golden fixtures for every C14 rule (design §5.4.1, §5.10; AT-18 to AT-21, AT-23)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cdmc_common import aspects
from cdmc_common.names import TableRef
from evaluator.c14.module import ASPECT, VERSION, C14Module
from tests import estate
from tests.fakes import FakeDomains, FakeLineage, FakeStore, make_deps

GOLDEN = sorted((Path(__file__).parent / "golden" / "c14").glob("*.json"))


def _now(fx: dict) -> datetime:
    return datetime.fromisoformat(fx["now"].replace("Z", "+00:00"))


def run_fixture(fx: dict):
    now = _now(fx)
    domains, bindings = estate.domains_and_bindings()
    objects = {
        f"lineage/dev/{d}/manifest.json": m for d, m in fx.get("manifests", {}).items()
    }
    if fx.get("products"):
        objects["products/dev/customer-dev/customer-profile.json"] = (
            estate.published_customer_profile()
        )
    deps = make_deps(
        now,
        lineage=FakeLineage([tuple(e) for e in fx["edges"]]),
        domains=FakeDomains(domains, bindings),
        store=FakeStore(objects),
    )
    ref = deps.bigquery.add_table(
        fx["table"]["ref"], now - timedelta(hours=fx["table"]["created_hours_ago"])
    )
    finding = C14Module(deps).evaluate(ref, "EVENT", "msg-1")
    return deps, ref, finding


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_c14_golden(path: Path):
    fx = json.loads(path.read_text())
    deps, ref, finding = run_fixture(fx)
    exp = fx["expected"]
    d = finding.evaluation.detail
    assert d["control_status"] == exp["control_status"]
    assert finding.evaluation.status == exp["status"]
    assert d["rule"] == exp["rule"]
    assert d["run_time_ultimate_source"] == exp["run_time_ultimate_source"]
    assert d["design_time_ultimate_source"] == exp["design_time_ultimate_source"]
    assert d["upstream_data_products"] == exp["upstream_data_products"]
    assert d["undeclared_sources"] == exp["undeclared_sources"]
    assert finding.control == "C14" and finding.check == "C14.lineage"
    assert finding.evaluation.evaluator_version == VERSION
    assert finding.narrative is None  # D-01: no narrative in phase 1

    # Evidence aspect: one whole-aspect write on the table entry, valid against §5.11.4.
    (entry, key, data), = deps.catalog.writes
    assert entry == ref.entry_name("australia-southeast1")
    assert key == "kcdda-dgov-hub-nonprod.global.cdmc-lineage-evidence"
    aspects.validate(ASPECT, data)
    if exp["run_time_ultimate_source"]:
        assert data["run_time_ultimate_source"] == exp["run_time_ultimate_source"]
    else:
        assert "run_time_ultimate_source" not in data
    if exp["design_time_ultimate_source"] is None:
        assert "design_time_ultimate_source" not in data
    else:
        assert data["design_time_ultimate_source"] == exp["design_time_ultimate_source"]
    assert data["upstream_data_products"] == exp["upstream_data_products"]
    assert data["evaluation"]["evaluator_version"] == VERSION


def test_rule_order_is_fixed():
    from evaluator.c14 import rules

    assert rules.CONTROL_STATUSES == (
        "PENDING", "INSUFFICIENT_EVIDENCE", "MISMATCH", "VERIFIED"
    )


def test_lineage_requested_one_beyond_hop_limit():
    fx = json.loads((GOLDEN[0]).read_text())
    deps, ref, _ = run_fixture(fx)
    assert deps.lineage.calls == [(TableRef.parse(fx["table"]["ref"]).fqn, 21)]


def test_redelivered_event_gives_same_finding_id():
    fx = json.loads(GOLDEN[0].read_text())
    _, _, f1 = run_fixture(fx)
    _, _, f2 = run_fixture(fx)
    assert f1.finding_id == f2.finding_id


def test_pending_boundary_is_48_hours():
    fx = json.loads(GOLDEN[0].read_text())
    fx["table"]["created_hours_ago"] = 48
    _, _, f = run_fixture(fx)
    assert f.evaluation.detail["control_status"] == "INSUFFICIENT_EVIDENCE"
    fx["table"]["created_hours_ago"] = 47.99
    _, _, f = run_fixture(fx)
    assert f.evaluation.detail["control_status"] == "PENDING"
