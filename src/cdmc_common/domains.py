"""Data domains adapter (design §2.3; Preview).

Data domains are Preview with no Terraform or client-library support (AGENTS §2.2), so this
module is the only code that calls the `dataDomains` REST surface. It returns objects in the
native API shape (`name`, `parentDataDomain`, `contacts`, `policyMember`, ...; bindings with
`name` and `resource`), so callers do not change when a GA client arrives.

Note the platform gap (AGENTS §2.4): bindings are not re-synchronised with IAM; the
reconciler (phase 2) is the compensating control. Phase 1 code only reads.
"""

from __future__ import annotations

from typing import Any, Protocol

from cdmc_common.transport import Transport

DATAPLEX = "https://dataplex.googleapis.com/v1"


class DomainsAdapter(Protocol):
    def list_domains(self) -> list[dict[str, Any]]:
        """Native `DataDomain` objects in the hub location."""
        ...

    def list_bindings(self, domain_name: str) -> list[dict[str, Any]]:
        """Native `DataDomainBinding` objects of one domain or subdomain."""
        ...


class RestDomainsAdapter:
    def __init__(self, transport: Transport, hub_project: str, location: str):
        self._t = transport
        self._parent = f"projects/{hub_project}/locations/{location}"

    def list_domains(self) -> list[dict[str, Any]]:
        # VERIFY IN BUILD (VA-48): domains list is readable by evaluator-<env> through
        # roles/dataplex.catalogViewer (or the domain viewer role) in the hub.
        return self._t.paged(f"{DATAPLEX}/{self._parent}/dataDomains", "dataDomains")

    def list_bindings(self, domain_name: str) -> list[dict[str, Any]]:
        return self._t.paged(f"{DATAPLEX}/{domain_name}/bindings", "dataDomainBindings")


def top_domain_id(domain: dict[str, Any], by_name: dict[str, dict[str, Any]]) -> str:
    """Walk `parentDataDomain` to the top-level domain and return its short ID."""
    seen: set[str] = set()
    current = domain
    while current.get("parentDataDomain") and current["name"] not in seen:
        seen.add(current["name"])
        parent = by_name.get(current["parentDataDomain"])
        if parent is None:
            break
        current = parent
    return current["name"].rsplit("/", 1)[-1]


def resource_domain_index(adapter: DomainsAdapter) -> dict[str, str]:
    """Map each bound resource (`//bigquery.googleapis.com/projects/p/datasets/d`, products)
    to the ID of its top-level domain (`customer-dev`)."""
    domains = adapter.list_domains()
    by_name = {d["name"]: d for d in domains}
    index: dict[str, str] = {}
    for d in domains:
        owner = top_domain_id(d, by_name)
        for b in adapter.list_bindings(d["name"]):
            if b.get("resource"):
                index[b["resource"]] = owner
    return index
