"""Published product definitions in the hub state bucket (design §3.2.1).

Path: `products/<env>/<domain>/<product-id>.json`, schema `published_product.schema.json`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cdmc_common.names import TableRef, product_resource
from cdmc_common.storage import StateStore


@dataclass(frozen=True)
class PublishedProduct:
    path: str
    domain: str
    product_id: str
    data: dict[str, Any]

    @property
    def key(self) -> str:
        """`<env>/<domain>/<product-id>`: the rollup task key."""
        return f"{self.data['env']}/{self.domain}/{self.product_id}"

    @property
    def tables(self) -> list[TableRef]:
        return [TableRef.parse(a["table_resource"]) for a in self.data["assets"].values()]

    def resource_name(self, location: str) -> str:
        """The product lives in the domain's analytics project, where its assets are.

        VERIFY IN BUILD (VA-56): product location equals the hub region.
        """
        project = self.tables[0].project if self.tables else ""
        return product_resource(project, location, self.product_id)


def parse_product_path(path: str) -> tuple[str, str, str] | None:
    """`products/dev/customer-dev/customer-profile.json` -> (env, domain, id)."""
    parts = path.split("/")
    if len(parts) != 4 or parts[0] != "products" or not parts[3].endswith(".json"):
        return None
    return parts[1], parts[2], parts[3].removesuffix(".json")


def parse_lineage_path(path: str) -> tuple[str, str] | None:
    """`lineage/dev/customer-dev/manifest.json` -> (env, domain)."""
    parts = path.split("/")
    if len(parts) != 4 or parts[0] != "lineage" or parts[3] != "manifest.json":
        return None
    return parts[1], parts[2]


class ProductRegistry:
    """All published products of one environment, indexed by asset table."""

    def __init__(self, store: StateStore, env: str):
        self._store = store
        self._env = env
        self._products: list[PublishedProduct] | None = None

    def load_one(self, key: str) -> PublishedProduct | None:
        env, domain, pid = key.split("/")
        path = f"products/{env}/{domain}/{pid}.json"
        data = self._store.read_json(path)
        return None if data is None else PublishedProduct(path, domain, pid, data)

    def all(self) -> list[PublishedProduct]:
        if self._products is None:
            out = []
            for path in sorted(self._store.list(f"products/{self._env}/")):
                parsed = parse_product_path(path)
                if not parsed:
                    continue
                data = self._store.read_json(path)
                if data is not None:
                    out.append(PublishedProduct(path, parsed[1], parsed[2], data))
            self._products = out
        return self._products

    def products_of_table(self, ref: TableRef) -> list[PublishedProduct]:
        return [p for p in self.all() if ref in p.tables]
