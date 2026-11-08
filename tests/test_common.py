"""cdmc_common: names, findings, status mapping vs aspect enums, aspect validation,
lineage stream parsing, debounce IDs, domain index and settings."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cdmc_common import aspects
from cdmc_common.config import Settings
from cdmc_common.domains import resource_domain_index
from cdmc_common.findings import CONTROL_STATUS_MAP, make_finding, map_status
from cdmc_common.lineage import parse_stream
from cdmc_common.names import TableRef, domain_pipeline_identities, gcs_fqn_matches
from cdmc_common.tasks import rollup_task_id
from evaluator.c14 import rules as c14
from tests import estate
from tests.fakes import FakeDomains

NOW = datetime(2026, 11, 9, tzinfo=UTC)


def test_status_mapping_matches_aspect_enums():
    assert set(CONTROL_STATUS_MAP["C12.product_status"]) == set(
        aspects.enum_values("cdmc-data-product-quality", "aggregate_dq_status"))
    assert set(CONTROL_STATUS_MAP["C12.freshness"]) == set(
        aspects.enum_values("cdmc-data-product-quality", "freshness_status"))
    assert set(CONTROL_STATUS_MAP["C14.lineage"]) == set(c14.CONTROL_STATUSES)
    assert map_status("C12.product_status", "NOT_EVALUATED") == "FAIL"
    assert map_status("C14.lineage", "PENDING") == "PENDING"


def test_aspect_templates_indexes_and_shared_fields():
    for name, tpl in aspects.templates().items():
        fields = tpl["recordFields"]
        idx = [f["index"] for f in fields]
        assert idx == sorted(idx) and len(set(idx)) == len(idx), name
        names = [f["name"] for f in fields]
        assert {"evaluation", "narrative", "schema_version"} <= set(names), name


def test_aspect_validation_rejects_bad_payloads():
    good = {"aggregate_dq_status": "COMPLIANT",
            "evaluation": aspects.evaluation_record("v", "2026-11-09T00:00:00Z", "r"),
            "schema_version": 1}
    aspects.validate("cdmc-data-product-quality", good)
    for bad in (
        {**good, "aggregate_dq_status": "GREEN"},
        {**good, "extra": 1},
        {k: v for k, v in good.items() if k != "evaluation"},
        {**good, "aggregate_dq_score": None},
        {**good, "evaluation": {**good["evaluation"], "evaluated_at": "yesterday"}},
    ):
        with pytest.raises(aspects.AspectValidationError):
            aspects.validate("cdmc-data-product-quality", bad)


def test_finding_message_shape_and_d01_split():
    f = make_finding(env="dev", control="C14", check="C14.lineage", asset="//x", asset_type="TABLE",
                     status="PASS", evaluator_version="c14-lineage@1.0.0", trigger="EVENT",
                     trigger_id="m", source_ref="r", now=NOW, detail={"control_status": "VERIFIED"})
    msg = f.to_message()
    assert "narrative" not in msg  # P5-2: narratives go to cdmc_report.narratives
    assert json.loads(msg["evaluation"]["detail"]) == {"control_status": "VERIFIED"}
    assert set(msg) == {"finding_id", "environment", "control", "check", "asset", "asset_type",
                        "domain", "product", "detected_at", "evaluation", "route"}
    rec = f.to_record()
    assert rec["narrative"] is None
    with pytest.raises(ValueError):
        make_finding(env="dev", control="C14", check="c", asset="a", asset_type="TABLE",
                     status="GREEN", evaluator_version="v", trigger="EVENT", trigger_id="t",
                     source_ref="r", now=NOW)


def test_table_ref_round_trips():
    ref = TableRef.parse("p.d.t")
    assert TableRef.parse(ref.resource) == ref == TableRef.parse(ref.fqn)
    assert TableRef.from_entry_name(ref.entry_name("australia-southeast1")) == ref
    with pytest.raises(ValueError):
        TableRef.parse("not-a-table")


def test_pipeline_identities():
    ids = domain_pipeline_identities("customer-dev", "pltfrm-ngnrng", True)
    assert ids == {"tf-customer-nonprod@pltfrm-ngnrng.iam.gserviceaccount.com",
                   "tf-deployer@pltfrm-ngnrng.iam.gserviceaccount.com"}
    assert "tf-deployer@pltfrm-ngnrng.iam.gserviceaccount.com" not in (
        domain_pipeline_identities("customer-dev", "pltfrm-ngnrng", False))


def test_gcs_matching():
    assert gcs_fqn_matches("gcs:bkt.landing/contacts/a.csv", "gs://bkt/landing/contacts/")
    assert gcs_fqn_matches("gcs:bkt/landing/contacts/a.csv", "gs://bkt/landing/contacts/")
    assert not gcs_fqn_matches("gcs:bkt.other/a.csv", "gs://bkt/landing/")
    assert gcs_fqn_matches("gcs:bkt.anything", "gs://bkt")


def test_parse_stream_accepts_array_and_object():
    link = {"source": {"fullyQualifiedName": "a"}, "target": {"fullyQualifiedName": "b"},
            "depth": 1}
    assert len(parse_stream([{"links": [link]}, {"links": [link]}])) == 2
    assert len(parse_stream({"links": [link]})) == 1
    assert parse_stream(None) == []


def test_rollup_task_ids_bucket_by_window():
    a, end = rollup_task_id("dev/customer-dev/customer-profile", NOW, 300)
    b, _ = rollup_task_id("dev/customer-dev/customer-profile", NOW.replace(minute=4), 300)
    c, _ = rollup_task_id("dev/customer-dev/customer-profile", NOW.replace(minute=5), 300)
    assert a == b != c and end == NOW.replace(minute=5)


def test_domain_index_maps_bound_datasets_to_top_domain():
    domains, bindings = estate.domains_and_bindings()
    idx = resource_domain_index(FakeDomains(domains, bindings))
    assert idx[f"//bigquery.googleapis.com/projects/{estate.CORE}/datasets/customer_curated"] == (
        "customer-dev")
    assert idx[f"//bigquery.googleapis.com/projects/{estate.ACCT_AN}/datasets/accounts_analytics"] == (
        "accounts-dev")


def test_settings_from_env():
    s = Settings.from_env({"HUB_PROJECT_ID": "h", "DATA_PROJECTS": "a,b", "SINGLE_DEPLOYER": "false"})
    assert s.hub_state_bucket == "h-dda-state" and s.data_projects == ["a", "b"]
    assert not s.single_deployer and s.tier == "nonprod"
    assert s.aspect_key("cdmc-lineage-evidence") == "h.global.cdmc-lineage-evidence"
