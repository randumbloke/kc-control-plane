"""Evaluator sweep: the daily Cloud Run job (design §5.1, §2.5).

Task i of N evaluates the data projects whose SHA-256 hash modulo N is i, and the products
whose key hashes to i. It covers every asset whether or not an event arrived (AT-13), and
reprocesses anything dead-lettered. An asset whose inputs are unavailable after retries gets
a NOT_EVALUATED finding and the sweep continues; the task exits non-zero if any did.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys

from cdmc_common.findings import make_finding
from evaluator.deps import Deps, build_deps
from evaluator.handlers import Evaluator

log = logging.getLogger("evaluator.sweep")


def shard(key: str, count: int) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % count


def run_sweep(deps: Deps, task_index: int, task_count: int, execution: str) -> dict[str, int]:
    s = deps.settings
    ev = Evaluator(deps)
    trigger_id = f"sweep-{deps.clock().date().isoformat()}-{execution}"
    stats = {"tables": 0, "products": 0, "findings": 0, "errors": 0}

    def not_evaluated(control: str, check: str, asset: str, asset_type: str, err: Exception):
        stats["errors"] += 1
        log.exception("evaluation failed for %s %s", check, asset)
        deps.publisher.publish(
            make_finding(
                env=s.env,
                control=control,
                check=check,
                asset=asset,
                asset_type=asset_type,
                status="NOT_EVALUATED",
                evaluator_version="evaluator-sweep@1.0.0",
                trigger="SWEEP",
                trigger_id=trigger_id,
                source_ref=asset,
                now=deps.clock(),
                detail={"error": type(err).__name__, "message": str(err)[:500]},
            )
        )

    for project in sorted(s.data_projects):
        if shard(project, task_count) != task_index:
            continue
        for ds in deps.bigquery.list_datasets(project):
            for ref in deps.bigquery.list_tables(project, ds.dataset):
                stats["tables"] += 1
                try:
                    stats["findings"] += ev.evaluate_table(ref, "SWEEP", trigger_id).findings
                except Exception as e:
                    not_evaluated("C14", "C14.lineage", ref.resource, "TABLE", e)

    for product in deps.registry.all():
        if shard(product.key, task_count) != task_index:
            continue
        stats["products"] += 1
        try:
            stats["findings"] += ev.rollup(product.key, "SWEEP", trigger_id).findings
        except Exception as e:
            asset = product.resource_name(s.region)
            not_evaluated("C12", "C12.product_status", asset, "DATA_PRODUCT", e)
    log.info("sweep task %d/%d done: %s", task_index, task_count, stats)
    return stats


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    index = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    count = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    execution = os.environ.get("CLOUD_RUN_EXECUTION", "local")
    stats = run_sweep(build_deps(), index, count, execution)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
