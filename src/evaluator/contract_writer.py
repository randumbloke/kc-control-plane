"""Contract writer (design §3.2.1, §5.11.5).

The only writer of `cdmc-data-product-contract`. Triggered by the upload of a published
product definition, it checks the uploader, rereads the published file, writes the whole
aspect (never merging with what is there) and enqueues a product rollup so a changed
threshold is applied without another event (§5.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cdmc_common import aspects
from cdmc_common.findings import Finding, iso, make_finding
from cdmc_common.products import PublishedProduct, parse_product_path
from evaluator.deps import Deps
from evaluator.uploader import StateUpload, uploader_allowed
from kc_validate import schemas

VERSION = "contract-writer@1.0.0"
ASPECT = "cdmc-data-product-contract"
log = logging.getLogger(__name__)


@dataclass
class ContractResult:
    written: bool
    reason: str
    aspect: dict[str, Any] | None = None
    finding: Finding | None = None
    rollup_task: str | None = None


def build_contract_aspect(data: dict[str, Any], source_ref: str, evaluated_at: str) -> dict:
    """Whole `cdmc-data-product-contract` aspect from a published product definition."""
    aspect: dict[str, Any] = {}
    if data.get("aggregate_dq_threshold") is not None:
        aspect["aggregate_dq_threshold"] = float(data["aggregate_dq_threshold"])
    if data.get("steward_group"):
        aspect["steward"] = data["steward_group"]
    aspect["evaluation"] = aspects.evaluation_record(VERSION, evaluated_at, source_ref)
    aspect["schema_version"] = aspects.SCHEMA_VERSION
    return aspects.validate(ASPECT, aspect)


class ContractWriter:
    def __init__(self, deps: Deps):
        self.d = deps

    def _reject(
        self,
        upload: StateUpload,
        domain: str | None,
        product_id: str | None,
        reason: str,
        status: str = "FAIL",
    ) -> ContractResult:
        s = self.d.settings
        f = make_finding(
            env=s.env,
            control="C12",
            check="C12.contract_source",
            asset=f"gs://{upload.bucket}/{upload.path}",
            asset_type="DATA_PRODUCT",
            status=status,
            evaluator_version=VERSION,
            trigger="EVENT",
            trigger_id=upload.event_id,
            source_ref=f"gs://{upload.bucket}/{upload.path}",
            now=self.d.clock(),
            domain=domain,
            product=product_id,
            detail={"reason": reason, "uploader": upload.uploader},
        )
        self.d.publisher.publish(f)
        log.warning("contract not written for %s: %s", upload.path, reason)
        return ContractResult(False, reason, finding=f)

    def handle(self, upload: StateUpload) -> ContractResult:
        s = self.d.settings
        parsed = parse_product_path(upload.path)
        if parsed is None or parsed[0] != s.env:
            return ContractResult(False, "not a product definition of this environment")
        env, domain, product_id = parsed
        if not uploader_allowed(domain, upload.uploader, s.cicd_project_id, s.single_deployer):
            return self._reject(
                upload,
                domain,
                product_id,
                f"uploader {upload.uploader!r} is not the pipeline of {domain}",
            )
        data = self.d.store.read_json(upload.path)
        if data is None:
            return ContractResult(False, "object no longer exists")
        errs = schemas.errors("published_product", data)
        if errs:
            return self._reject(upload, domain, product_id, "schema: " + "; ".join(errs[:5]))
        if (data["env"], data["domain"], data["product_id"]) != (env, domain, product_id):
            return self._reject(
                upload,
                domain,
                product_id,
                "env, domain or product_id does not match the object path",
            )
        entry = data.get("dataplex_entry")
        if not entry:
            return self._reject(
                upload,
                domain,
                product_id,
                "published product has no dataplex_entry yet",
                "NOT_EVALUATED",
            )
        now = self.d.clock()
        source_ref = f"gs://{upload.bucket}/{upload.path}"
        aspect = build_contract_aspect(data, source_ref, iso(now))
        self.d.catalog.write_aspect(entry, s.aspect_key(ASPECT), aspect)
        product = PublishedProduct(upload.path, domain, product_id, data)
        task = self.d.queue.enqueue(product.key, now)
        log.info("contract written for %s; rollup %s", product.key, task or "already queued")
        return ContractResult(True, "written", aspect=aspect, rollup_task=task)
