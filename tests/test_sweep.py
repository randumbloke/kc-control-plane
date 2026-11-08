"""Daily sweep (AT-13): every table of every data project, every product, sharded."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from evaluator.sweep import run_sweep, shard
from tests import estate
from tests.fakes import FakeStore, make_deps

NOW = datetime(2026, 11, 9, 15, tzinfo=UTC)


def deps():
    d = make_deps(NOW, store=FakeStore({
        "products/dev/customer-dev/customer-profile.json": estate.published_customer_profile()}))
    d.bigquery.add_dataset(estate.CORE, "customer_curated", True)
    d.bigquery.add_dataset(estate.CORE, "accounts_curated", False)
    d.bigquery.add_dataset(estate.CUST_AN, "customer_analytics", True)
    d.bigquery.add_table(f"{estate.CORE}.customer_curated.contacts", NOW - timedelta(days=3))
    d.bigquery.add_table(f"{estate.CORE}.accounts_curated.balances", NOW - timedelta(days=3))
    d.bigquery.add_table(f"{estate.CUST_AN}.customer_analytics.customer_profile",
                         NOW - timedelta(days=3))
    d.catalog.entries[estate.CUSTOMER_PROFILE_ENTRY] = {"aspects": {}}
    return d


def test_single_task_covers_everything():
    d = deps()
    stats = run_sweep(d, 0, 1, "exec-1")
    assert stats == {"tables": 3, "products": 1, "findings": 3 + 2 + 1, "errors": 0}
    assert all(f.evaluation.trigger == "SWEEP" for f in d.publisher.findings)


def test_shards_partition_the_work():
    total_tables = total_products = 0
    for i in range(20):
        stats = run_sweep(deps(), i, 20, "exec-1")
        total_tables += stats["tables"]
        total_products += stats["products"]
    assert (total_tables, total_products) == (3, 1)


def test_failure_gives_not_evaluated_and_continues():
    d = deps()

    def boom(*_a, **_k):
        raise RuntimeError("lineage down")

    d.lineage.search_upstream = boom
    stats = run_sweep(d, 0, 1, "exec-1")
    assert stats["errors"] == 3
    ne = [f for f in d.publisher.findings if f.evaluation.status == "NOT_EVALUATED"]
    assert len(ne) == 3 and all(f.check == "C14.lineage" for f in ne)


def test_shard_is_stable():
    assert shard("kcdda-core-dwh-dev", 20) == shard("kcdda-core-dwh-dev", 20)
