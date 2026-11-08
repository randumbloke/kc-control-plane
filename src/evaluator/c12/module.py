"""C12 module: table coverage and the product rollup (design §5.4)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from cdmc_common import aspects
from cdmc_common.bigquery import DatasetInfo
from cdmc_common.catalog import SYSTEM_REFRESH_CADENCE, find_aspect
from cdmc_common.findings import Finding, iso, make_finding, map_status
from cdmc_common.names import TableRef
from cdmc_common.products import PublishedProduct
from cdmc_common.scans import ScanInfo
from cdmc_common.sensitivity import product_is_sensitive, table_is_sensitive
from evaluator.c12 import rules
from evaluator.deps import Deps
from evaluator.route import product_route

COVERAGE_VERSION = "c12-coverage@1.0.0"
ROLLUP_VERSION = "c12-rollup@1.0.0"
QUALITY_ASPECT = "cdmc-data-product-quality"
CONTRACT_ASPECT = "cdmc-data-product-contract"
RESULTS_LOOKBACK_DAYS = 35
log = logging.getLogger(__name__)

# Latest rule results per table from the central export (design §3.6, `scan_results`).
# VERIFY IN BUILD (VA-35): one central export table for scans in many projects, and the
# export column names used here. VERIFY IN BUILD (VA-34): the `cdmc-critical` rule attribute
# survives to the export as `rule_attributes`; if not, critical rules are read from the scan.
RESULTS_SQL = """
SELECT
  CONCAT(data_source.table_project_id, '.', data_source.dataset_id, '.',
         data_source.table_id) AS table_ref,
  data_quality_job_id AS job_id,
  CAST(job_end_time AS STRING) AS job_end_time,
  rule_name,
  CAST(rule_passed AS STRING) AS rule_passed,
  JSON_VALUE(TO_JSON_STRING(rule_attributes), '$."cdmc-critical"') AS critical
FROM `{table}`
WHERE job_end_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
  AND CONCAT(data_source.table_project_id, '.', data_source.dataset_id, '.',
             data_source.table_id) IN UNNEST(@tables)
"""


def _ts(value: str) -> datetime:
    v = value.strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    if " " in v and "T" not in v:
        v = v.replace(" ", "T", 1)
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def latest_jobs(rows: list[dict[str, Any]]) -> dict[str, rules.JobResult]:
    """Group export rows by table and keep each table's latest job."""
    by_job: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["table_ref"], r["job_id"])
        job = by_job.setdefault(key, {"end": _ts(r["job_end_time"]), "rules": []})
        job["rules"].append(
            rules.RuleResult(
                name=r["rule_name"],
                passed=str(r["rule_passed"]).lower() == "true",
                critical=str(r.get("critical") or "").lower() == "true",
            )
        )
    latest: dict[str, rules.JobResult] = {}
    for (table, job_id), job in sorted(by_job.items(), key=lambda kv: (kv[1]["end"], kv[0][1])):
        latest[table] = rules.JobResult(job_id, job["end"], tuple(job["rules"]))
    return latest


class C12Module:
    def __init__(self, deps: Deps):
        self.d = deps
        self._scans: dict[str, dict[TableRef, ScanInfo]] = {}
        self._datasets: dict[tuple[str, str], DatasetInfo | None] = {}

    # ---- inputs -----------------------------------------------------------------------
    def central_export_resource(self) -> str:
        p, ds, t = self.d.settings.dq_results_table.split(".")
        return f"//bigquery.googleapis.com/projects/{p}/datasets/{ds}/tables/{t}"

    def scan_of(self, ref: TableRef) -> ScanInfo | None:
        if ref.project not in self._scans:
            self._scans[ref.project] = {
                s.table: s for s in self.d.scans.list_quality_scans(ref.project)
            }
        return self._scans[ref.project].get(ref)

    def scan_state(self, ref: TableRef) -> rules.ScanState:
        scan = self.scan_of(ref)
        if scan is None:
            return rules.ScanState(False)
        central = self.central_export_resource()
        export = scan.export_table or ""
        exports = export.removeprefix("//bigquery.googleapis.com/") == central.removeprefix(
            "//bigquery.googleapis.com/"
        )
        return rules.ScanState(True, scan.publishing_enabled, exports)

    def dataset(self, ref: TableRef) -> DatasetInfo | None:
        key = (ref.project, ref.dataset)
        if key not in self._datasets:
            self._datasets[key] = self.d.bigquery.get_dataset(ref.project, ref.dataset)
        return self._datasets[key]

    def table_is_sensitive(self, ref: TableRef) -> bool:
        """Phase 1 sensitivity test (P5-8c without classifier): the dataset label
        `cdmc-sensitivity=sensitive`. Column levels join when the classifier is on (phase 2)."""
        ds = self.dataset(ref)
        return table_is_sensitive(ds.labels if ds else None)

    # ---- coverage ---------------------------------------------------------------------
    def coverage(
        self, ref: TableRef, trigger: str, trigger_id: str, domain: str | None = None
    ) -> Finding:
        now = self.d.clock()
        ok, reason = rules.coverage(self.scan_state(ref))
        scan = self.scan_of(ref)
        products = self.d.registry.products_of_table(ref)
        finding = make_finding(
            env=self.d.settings.env,
            control="C12",
            check="C12.coverage",
            asset=ref.resource,
            asset_type="TABLE",
            status="PASS" if ok else "FAIL",
            evaluator_version=COVERAGE_VERSION,
            trigger=trigger,
            trigger_id=trigger_id,
            source_ref=scan.name if scan else ref.resource,
            now=now,
            domain=domain,
            product=products[0].product_id if products else None,
            detail={
                "reason": reason,
                "scan": scan.name if scan else None,
                "export_table": scan.export_table if scan else None,
            },
            route=product_route(products[0].data) if products else None,
        )
        self.d.publisher.publish(finding)
        return finding

    # ---- rollup -----------------------------------------------------------------------
    def product_tables(self, product: PublishedProduct) -> list[tuple[TableRef, bool]]:
        """Asset tables with their sensitivity. A view asset is measured on its base tables
        (design §3.4), taken from one hop of run-time lineage. VERIFY IN BUILD (VA-47)."""
        product_sensitive = product_is_sensitive(self.table_is_sensitive(t) for t in product.tables)
        out: dict[TableRef, bool] = {}
        for asset in product.data["assets"].values():
            ref = TableRef.parse(asset["table_resource"])
            if asset["form"] == "pipeline_table":
                out[ref] = out.get(ref, False) or self.table_is_sensitive(ref)
                continue
            for link in self.d.lineage.search_upstream(ref.fqn, 1):
                if link.target == ref.fqn and link.source.startswith("bigquery:"):
                    base = TableRef.parse(link.source)
                    out[base] = (
                        out.get(base, False) or product_sensitive or (self.table_is_sensitive(base))
                    )
        return sorted(out.items())

    def rollup(self, product: PublishedProduct, trigger: str, trigger_id: str) -> list[Finding]:
        s = self.d.settings
        now = self.d.clock()
        entry_name = product.data.get("dataplex_entry")
        route = product_route(product.data)
        product_name = product.resource_name(s.region)
        common = dict(
            env=s.env,
            control="C12",
            asset=product_name,
            asset_type="DATA_PRODUCT",
            evaluator_version=ROLLUP_VERSION,
            trigger=trigger,
            trigger_id=trigger_id,
            now=now,
            domain=product.domain,
            product=product.product_id,
            route=route,
        )
        if not entry_name:
            f = make_finding(
                check="C12.product_status",
                status="NOT_EVALUATED",
                source_ref=product.path,
                detail={"reason": "published product has no dataplex_entry"},
                **common,
            )
            self.d.publisher.publish(f)
            return [f]

        entry = self.d.catalog.lookup_entry(entry_name)
        contract = find_aspect(entry, CONTRACT_ASPECT) or {}
        threshold = contract.get("aggregate_dq_threshold")
        cadence = rules.Cadence.from_aspect(find_aspect(entry, SYSTEM_REFRESH_CADENCE))

        refs = self.product_tables(product)
        dotted = [r.dotted for r, _ in refs]
        rows = (
            self.d.bigquery.query(
                RESULTS_SQL.format(table=s.dq_results_table, days=RESULTS_LOOKBACK_DAYS),
                {"tables": dotted},
            )
            if dotted
            else []
        )
        jobs = latest_jobs(rows)
        tables = []
        for ref, sensitive in refs:
            info = self.d.bigquery.get_table(ref)
            tables.append(
                rules.ProductTable(
                    resource=ref.resource,
                    sensitive=sensitive,
                    last_modified=info.last_modified if info else None,
                    scan=self.scan_state(ref),
                    latest_job=jobs.get(ref.dotted),
                )
            )
        result = rules.rollup(
            tables=tables, threshold=threshold, cadence=cadence, now=now, tz=s.scheduler_time_zone
        )

        job_refs = sorted(j.job_id for j in jobs.values())
        source_ref = job_refs[-1] if job_refs else (entry_name or product.path)
        data: dict[str, Any] = {}
        if result.score is not None:
            data["aggregate_dq_score"] = float(result.score)
        data["aggregate_dq_status"] = result.status
        if result.freshness is not None:
            data["freshness_status"] = result.freshness
        data["evaluation"] = aspects.evaluation_record(ROLLUP_VERSION, iso(now), source_ref)
        data["schema_version"] = aspects.SCHEMA_VERSION
        aspects.validate(QUALITY_ASPECT, data)
        self.d.catalog.write_aspect(entry_name, s.aspect_key(QUALITY_ASPECT), data)

        detail = {
            "control_status": result.status,
            "reasons": result.reasons,
            "rules_passed": result.rules_passed,
            "rules_evaluated": result.rules_evaluated,
            "failed_critical": result.failed_critical,
            "jobs": job_refs,
        }
        findings = [
            make_finding(
                check="C12.product_status",
                status=map_status("C12.product_status", result.status),
                source_ref=source_ref,
                measured_value=result.score,
                threshold=float(threshold) if threshold is not None else None,
                detail=detail,
                **common,
            )
        ]
        if result.freshness is not None:
            findings.append(
                make_finding(
                    check="C12.freshness",
                    status=map_status("C12.freshness", result.freshness),
                    source_ref=entry_name,
                    detail={
                        "control_status": result.freshness,
                        "late_tables": result.late_tables,
                        "threshold_minutes": cadence.threshold_minutes if cadence else None,
                    },
                    **common,
                )
            )
        for f in findings:
            self.d.publisher.publish(f)
        return findings
