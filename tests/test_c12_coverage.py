"""C12 coverage (AT-14) through the evaluator's table path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from evaluator.handlers import Evaluator
from tests.fakes import make_deps

NOW = datetime(2026, 11, 9, tzinfo=UTC)
T = "kcdda-core-dwh-dev.customer_curated.contacts"


def setup(sensitive=True):
    deps = make_deps(NOW)
    deps.bigquery.add_dataset("kcdda-core-dwh-dev", "customer_curated", sensitive)
    ref = deps.bigquery.add_table(T, NOW - timedelta(days=5))
    return deps, ref


def coverage_of(deps, ref):
    Evaluator(deps).evaluate_table(ref, "SWEEP", "s1")
    return deps.publisher.by_check("C12.coverage")


def test_sensitive_table_without_scan_fails():
    deps, ref = setup()
    (f,) = coverage_of(deps, ref)
    assert f.evaluation.status == "FAIL"
    assert f.evaluation.detail["reason"] == "no data quality scan"


def test_scan_publishes_but_does_not_export_fails():
    deps, ref = setup()
    deps.scans.add(T, publishing=True, export=None)
    (f,) = coverage_of(deps, ref)
    assert f.evaluation.status == "FAIL"
    assert "export" in f.evaluation.detail["reason"]


def test_scan_exports_elsewhere_fails():
    deps, ref = setup()
    deps.scans.add(T, export="//bigquery.googleapis.com/projects/x/datasets/y/tables/z")
    (f,) = coverage_of(deps, ref)
    assert f.evaluation.status == "FAIL"


def test_scan_without_publishing_fails():
    deps, ref = setup()
    deps.scans.add(T, publishing=False)
    (f,) = coverage_of(deps, ref)
    assert f.evaluation.detail["reason"] == "scan does not publish to the catalogue"


def test_complete_scan_passes():
    deps, ref = setup()
    deps.scans.add(T)
    (f,) = coverage_of(deps, ref)
    assert f.evaluation.status == "PASS"


def test_non_sensitive_table_is_out_of_coverage_scope():
    deps, ref = setup(sensitive=False)
    assert coverage_of(deps, ref) == []
    # C14 still evaluates every table, sensitive or not.
    assert len(deps.publisher.by_check("C14.lineage")) == 1
