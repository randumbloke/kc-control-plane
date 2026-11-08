"""Dependencies of the evaluator, built once per process; tests pass fakes instead."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from cdmc_common.bigquery import BigQueryClient, RestBigQueryClient
from cdmc_common.catalog import CatalogClient, RestCatalogClient
from cdmc_common.config import Settings
from cdmc_common.domains import DomainsAdapter, RestDomainsAdapter, resource_domain_index
from cdmc_common.findings import FindingsPublisher, PubSubFindingsPublisher, utc_now
from cdmc_common.lineage import LineageClient, RestLineageClient
from cdmc_common.products import ProductRegistry
from cdmc_common.scans import RestScanClient, ScanClient
from cdmc_common.storage import GcsStateStore, StateStore
from cdmc_common.tasks import CloudTasksRollupQueue, RollupQueue
from cdmc_common.transport import Transport


@dataclass
class Deps:
    settings: Settings
    catalog: CatalogClient
    lineage: LineageClient
    domains: DomainsAdapter
    store: StateStore
    bigquery: BigQueryClient
    scans: ScanClient
    queue: RollupQueue
    publisher: FindingsPublisher
    clock: Callable[[], datetime] = utc_now
    _domain_index: dict[str, str] | None = field(default=None, repr=False)
    _registry: ProductRegistry | None = field(default=None, repr=False)

    @property
    def domain_index(self) -> dict[str, str]:
        """Bound resource -> top-level domain ID, read once per request or sweep."""
        if self._domain_index is None:
            self._domain_index = resource_domain_index(self.domains)
        return self._domain_index

    @property
    def registry(self) -> ProductRegistry:
        if self._registry is None:
            self._registry = ProductRegistry(self.store, self.settings.env)
        return self._registry

    def fresh(self) -> Deps:
        """Same clients, cleared caches (one per request)."""
        return Deps(
            self.settings,
            self.catalog,
            self.lineage,
            self.domains,
            self.store,
            self.bigquery,
            self.scans,
            self.queue,
            self.publisher,
            self.clock,
        )


def build_deps(settings: Settings | None = None) -> Deps:
    s = settings or Settings.from_env()
    t = Transport()
    return Deps(
        settings=s,
        catalog=RestCatalogClient(t, s.hub_project_id, s.region),
        lineage=RestLineageClient(t, s.hub_project_id, s.region),
        domains=RestDomainsAdapter(t, s.hub_project_id, s.region),
        store=GcsStateStore(t, s.hub_state_bucket),
        bigquery=RestBigQueryClient(t, s.hub_project_id, s.region),
        scans=RestScanClient(t, s.region),
        queue=CloudTasksRollupQueue(
            t, s.rollup_queue, s.rollup_handler_url, s.rollup_invoker_sa, s.rollup_window_seconds
        ),
        publisher=PubSubFindingsPublisher(t, s.findings_topic),
    )
