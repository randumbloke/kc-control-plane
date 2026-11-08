"""Design-time lineage manifests from the hub state bucket (design §5.4.1, P6-39).

Path `lineage/<env>/<domain>/manifest.json`, schema `lineage_manifest.schema.json`.
"""

from __future__ import annotations

from cdmc_common.names import TableRef
from cdmc_common.storage import StateStore
from evaluator.c14.rules import Declared


class ManifestIndex:
    def __init__(self, store: StateStore, env: str, bucket: str = ""):
        self._store = store
        self._env = env
        self._bucket = bucket
        self._by_domain: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._by_domain is None:
            out: dict[str, dict] = {}
            for path in sorted(self._store.list(f"lineage/{self._env}/")):
                parts = path.split("/")
                if len(parts) == 4 and parts[3] == "manifest.json":
                    data = self._store.read_json(path)
                    if data:
                        out[parts[2]] = data
            self._by_domain = out
        return self._by_domain

    def manifest_path(self, domain: str) -> str:
        return f"gs://{self._bucket}/lineage/{self._env}/{domain}/manifest.json"

    def lookup(self, ref: TableRef, domain: str | None) -> tuple[Declared | None, str | None]:
        """Design-time ultimate sources of a table and the domain whose manifest declared it.

        The manifest lists each target's direct sources and landing paths. A declared source
        that is itself a target of the same manifest is expanded through its own declaration,
        so the result is the set of design-time origins (leaf tables and landing paths), the
        same form as the run-time ultimate sources it is compared with (design §5.4.1).
        The table's own domain manifest is used when the domain is known; otherwise the first
        manifest (by domain ID) that declares the table.
        """
        manifests = self._load()
        order = [domain] if domain in manifests else sorted(manifests)
        for d in order:
            targets = manifests[d].get("targets", {})
            if ref.dotted in targets:
                return expand(targets, ref.dotted), d
        return None, None


def expand(targets: dict[str, dict], root: str) -> Declared:
    leaves: set[str] = set()
    landing: set[str] = set()
    seen: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        decl = targets.get(node)
        if decl is None:
            leaves.add(node)
            continue
        landing.update(decl.get("landing_paths", []))
        stack.extend(decl.get("sources", []))
    leaves.discard(root)
    return Declared(tuple(sorted(leaves)), tuple(sorted(landing)))
