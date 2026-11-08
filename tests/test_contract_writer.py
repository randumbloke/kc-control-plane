"""Contract writer (design §3.2.1, §2.3.2 uploader check, §5.11.5; AT-09)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from cdmc_common import aspects
from cdmc_common.config import Settings
from evaluator.contract_writer import ASPECT, ContractWriter
from evaluator.uploader import StateUpload
from tests import estate
from tests.fakes import FakeStore, make_deps

NOW = datetime(2026, 11, 9, 1, 2, 3, tzinfo=UTC)
PATH = "products/dev/customer-dev/customer-profile.json"
KEY = f"{estate.HUB}.global.{ASPECT}"


def upload(uploader=estate.DEPLOYER, path=PATH):
    return StateUpload(estate.BUCKET, path, uploader, "storage.objects.create", "evt-1")


def deps_with(data, settings=None):
    d = make_deps(NOW, store=FakeStore({PATH: data}), **({"settings": settings} if settings else {}))
    # The entry already holds other aspects the writer must not touch or merge with.
    d.catalog.entries[estate.CUSTOMER_PROFILE_ENTRY] = {
        "aspects": {KEY: {"data": {"aggregate_dq_threshold": 50.0, "steward": "old@x.com"}}}
    }
    return d


def test_writes_whole_aspect_and_enqueues_rollup():
    deps = deps_with(estate.published_customer_profile())
    res = ContractWriter(deps).handle(upload())
    assert res.written
    (entry, key, data), = deps.catalog.writes
    assert entry == estate.CUSTOMER_PROFILE_ENTRY and key == KEY
    assert data == {
        "aggregate_dq_threshold": 95.0,
        "steward": "dda-customer-dev-stewards@mikegyoung.joonix.net",
        "evaluation": {
            "evaluator_version": "contract-writer@1.0.0",
            "evaluated_at": "2026-11-09T01:02:03Z",
            "source_ref": f"gs://{estate.BUCKET}/{PATH}",
        },
        "schema_version": 1,
    }
    aspects.validate(ASPECT, data)
    assert len(deps.queue.tasks) == 1 and res.rollup_task


def test_changed_threshold_replaces_previous_value():
    deps = deps_with(estate.published_customer_profile(aggregate_dq_threshold=99.5))
    ContractWriter(deps).handle(upload())
    assert deps.catalog.entries[estate.CUSTOMER_PROFILE_ENTRY]["aspects"][KEY]["data"][
        "aggregate_dq_threshold"
    ] == 99.5


def test_domain_pipeline_accepted_when_not_single_deployer():
    s = dataclasses.replace(Settings(), single_deployer=False)
    deps = deps_with(estate.published_customer_profile(), s)
    ok = ContractWriter(deps).handle(
        upload("tf-customer-nonprod@pltfrm-ngnrng.iam.gserviceaccount.com"))
    assert ok.written


def test_tf_deployer_rejected_when_not_single_deployer():
    s = dataclasses.replace(Settings(), single_deployer=False)
    deps = deps_with(estate.published_customer_profile(), s)
    res = ContractWriter(deps).handle(upload())
    assert not res.written and deps.catalog.writes == []


def test_uploader_check_rejects_other_identity():
    deps = deps_with(estate.published_customer_profile())
    res = ContractWriter(deps).handle(
        upload("tf-accounts-nonprod@pltfrm-ngnrng.iam.gserviceaccount.com"))
    assert not res.written
    assert deps.catalog.writes == [] and deps.queue.tasks == []
    (f,) = deps.publisher.findings
    assert f.check == "C12.contract_source" and f.evaluation.status == "FAIL"
    assert "tf-accounts-nonprod" in f.evaluation.detail["uploader"]


def test_rejects_file_whose_content_names_another_domain():
    deps = deps_with(estate.published_customer_profile(domain="accounts-dev"))
    res = ContractWriter(deps).handle(upload())
    assert not res.written and "does not match" in res.reason


def test_rejects_invalid_published_file():
    data = estate.published_customer_profile()
    del data["owners_group"]
    deps = deps_with(data)
    res = ContractWriter(deps).handle(upload())
    assert not res.written and res.reason.startswith("schema:")


def test_missing_entry_is_not_evaluated_finding():
    deps = deps_with(estate.published_customer_profile(dataplex_entry=None))
    res = ContractWriter(deps).handle(upload())
    assert not res.written
    assert deps.publisher.findings[0].evaluation.status == "NOT_EVALUATED"


def test_other_environment_ignored():
    deps = deps_with(estate.published_customer_profile())
    res = ContractWriter(deps).handle(upload(path="products/prod/customer-prod/x.json"))
    assert not res.written and deps.publisher.findings == []
