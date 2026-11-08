"""C14 module: evaluate one table, write `cdmc-lineage-evidence`, publish one finding."""

from __future__ import annotations

import logging
from datetime import timedelta

from cdmc_common import aspects
from cdmc_common.catalog import find_aspect
from cdmc_common.findings import Finding, iso, make_finding, map_status
from cdmc_common.names import TableRef
from evaluator.c14 import rules
from evaluator.deps import Deps
from evaluator.manifests import ManifestIndex
from evaluator.route import product_route

VERSION = "c14-lineage@1.0.0"
ASPECT = "cdmc-lineage-evidence"
log = logging.getLogger(__name__)


class C14Module:
    def __init__(self, deps: Deps):
        self.d = deps
        s = deps.settings
        self.manifests = ManifestIndex(deps.store, s.env, s.hub_state_bucket)

    def domain_of_table(self, ref: TableRef) -> str | None:
        return self.d.domain_index.get(ref.dataset_resource)

    def _domain_of_fqn(self, fqn: str) -> str | None:
        try:
            return self.domain_of_table(TableRef.parse(fqn))
        except ValueError:
            return None

    def _provisioning_product(self, fqn: str) -> str | None:
        """Product whose asset `fqn` is, when the node is a provisioning point.

        Reads `cdmc-provisioning-point` (design §5.4.1). That aspect's writer, the C3 module,
        is a later phase (§6.17), so when the aspect is absent the module applies the C3
        definition itself: an asset of a published data product in an `analytics` dataset.
        """
        try:
            ref = TableRef.parse(fqn)
        except ValueError:
            return None
        products = self.d.registry.products_of_table(ref)
        if not products:
            return None
        entry = self.d.catalog.lookup_entry(ref.entry_name(self.d.settings.region))
        pp = find_aspect(entry, "cdmc-provisioning-point")
        if pp is not None:
            is_pp = bool(pp.get("is_provisioning_point"))
        else:
            is_pp = ref.dataset.endswith("_analytics")
        return products[0].resource_name(self.d.settings.region) if is_pp else None

    def evaluate(self, ref: TableRef, trigger: str, trigger_id: str) -> Finding | None:
        s = self.d.settings
        now = self.d.clock()
        info = self.d.bigquery.get_table(ref)
        if info is None:
            log.info("table %s no longer exists; skipped", ref.dotted)
            return None
        links = self.d.lineage.search_upstream(ref.fqn, s.lineage_max_hops + 1)
        res = rules.resolve(
            ref.fqn,
            links,
            max_hops=s.lineage_max_hops,
            domain_of=self._domain_of_fqn,
            provisioning_product=self._provisioning_product,
        )
        domain = self.domain_of_table(ref)
        declared, manifest_domain = self.manifests.lookup(ref, domain)
        outcome = rules.evaluate(
            created=info.created,
            now=now,
            resolution=res,
            declared=declared,
            pending_window=timedelta(hours=s.pending_window_hours),
        )
        source_ref = self.manifests.manifest_path(manifest_domain) if manifest_domain else ref.fqn
        data: dict = {}
        if res.sources:
            data["run_time_ultimate_source"] = res.sources
        if declared is not None:
            data["design_time_ultimate_source"] = declared.fqns()
        data["upstream_data_products"] = res.upstream_data_products
        data["evaluation"] = aspects.evaluation_record(VERSION, iso(now), source_ref)
        data["schema_version"] = aspects.SCHEMA_VERSION
        aspects.validate(ASPECT, data)
        self.d.catalog.write_aspect(ref.entry_name(s.region), s.aspect_key(ASPECT), data)

        products = self.d.registry.products_of_table(ref)
        finding = make_finding(
            env=s.env,
            control="C14",
            check="C14.lineage",
            asset=ref.resource,
            asset_type="TABLE",
            status=map_status("C14.lineage", outcome.status),
            evaluator_version=VERSION,
            trigger=trigger,
            trigger_id=trigger_id,
            source_ref=source_ref,
            now=now,
            domain=domain,
            product=products[0].product_id if products else None,
            detail={
                "control_status": outcome.status,
                "rule": outcome.rule,
                "age_hours": outcome.age_hours,
                "run_time_ultimate_source": res.sources,
                "design_time_ultimate_source": declared.fqns() if declared else None,
                "undeclared_sources": list(outcome.undeclared),
                "upstream_data_products": res.upstream_data_products,
                "truncated_paths": res.truncated_paths,
                "cycles": res.cycles,
            },
            route=product_route(products[0].data) if products else None,
        )
        self.d.publisher.publish(finding)
        return finding
