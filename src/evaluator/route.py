"""Finding routes (design §5.2 `route`). The C2 module is a later phase; until then the
route is the product's groups for product findings and the domain's contacts for tables."""

from __future__ import annotations

from typing import Any

from cdmc_common.findings import Route


def _pick(identities: list[dict[str, Any]], word: str) -> str | None:
    for i in identities:
        if word in str(i.get("role", "")).lower():
            return i.get("id") or i.get("identity") or i.get("name")
    return None


def domain_route(domain: dict[str, Any] | None) -> Route:
    if not domain:
        return Route()
    ids = (domain.get("contacts") or {}).get("identities", [])
    return Route(owner=_pick(ids, "owner"), steward=_pick(ids, "steward"))


def product_route(product_data: dict[str, Any]) -> Route:
    return Route(owner=product_data.get("owners_group"), steward=product_data.get("steward_group"))
