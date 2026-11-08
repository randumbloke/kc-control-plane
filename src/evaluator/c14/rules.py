"""C14 lineage resolution and status rules (design §5.4.1). Pure functions, no I/O.

Rules, first match wins, no pass-by-default branch:
  1 PENDING                table < 48 h old and no resolved run-time ultimate source
  2 INSUFFICIENT_EVIDENCE  table >= 48 h old and no resolved run-time ultimate source
  3 MISMATCH               a run-time ultimate source is not in the declared manifest set
  4 VERIFIED               >= 1 resolved source, all declared when a manifest entry exists

Resolution: follow upstream links from the table, at most `max_hops` hops, stopping at cycles.
A node with no upstream link is a run-time ultimate source. A path cut by the hop limit, or
one that only loops back on itself, ends at an intermediate node, which is not a source (this
is how "lineage that so far reaches only intermediate tables" is decided).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from cdmc_common.lineage import LineageLink
from cdmc_common.names import gcs_fqn_matches

PENDING = "PENDING"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
MISMATCH = "MISMATCH"
VERIFIED = "VERIFIED"
CONTROL_STATUSES = (PENDING, INSUFFICIENT_EVIDENCE, MISMATCH, VERIFIED)

MAX_PATH_VISITS = 100_000


@dataclass
class Resolution:
    has_lineage: bool
    sources: list[str]
    upstream_data_products: list[str]
    truncated_paths: int = 0
    cycles: int = 0
    intermediate_ends: list[str] = field(default_factory=list)


def resolve(
    root: str,
    links: Iterable[LineageLink],
    *,
    max_hops: int = 20,
    domain_of: Callable[[str], str | None] = lambda _fqn: None,
    provisioning_product: Callable[[str], str | None] = lambda _fqn: None,
) -> Resolution:
    upstream: dict[str, set[str]] = {}
    for link in links:
        # A self-link is kept: it is skipped as a cycle during the walk.
        upstream.setdefault(link.target, set()).add(link.source)
    if not upstream.get(root):
        return Resolution(has_lineage=False, sources=[], upstream_data_products=[])

    root_domain = domain_of(root)
    sources: set[str] = set()
    products: set[str] = set()
    intermediate: set[str] = set()
    state = {"truncated": 0, "cycles": 0, "visits": 0}

    def walk(node: str, depth: int, on_path: frozenset[str], crossed: bool, found: bool) -> None:
        state["visits"] += 1
        if state["visits"] > MAX_PATH_VISITS:
            state["truncated"] += 1
            intermediate.add(node)
            return
        ups = sorted(upstream.get(node, ()))
        if node != root and not ups:
            sources.add(node)
            return
        if depth >= max_hops:
            state["truncated"] += 1
            intermediate.add(node)
            return
        progressed = False
        for up in ups:
            if up in on_path:
                state["cycles"] += 1
                continue
            progressed = True
            dom = domain_of(up)
            crossed_up = crossed or (
                dom is not None and root_domain is not None and dom != root_domain
            )
            found_up = found
            if crossed_up and not found:
                product = provisioning_product(up)
                if product:
                    products.add(product)
                    found_up = True
            walk(up, depth + 1, on_path | {up}, crossed_up, found_up)
        if not progressed:
            intermediate.add(node)

    walk(root, 0, frozenset({root}), False, False)
    return Resolution(
        has_lineage=True,
        sources=sorted(sources),
        upstream_data_products=sorted(products),
        truncated_paths=state["truncated"],
        cycles=state["cycles"],
        intermediate_ends=sorted(intermediate),
    )


@dataclass(frozen=True)
class Declared:
    """A table's design-time declaration from the lineage manifest."""

    sources: tuple[str, ...]  # `project.dataset.table`
    landing_paths: tuple[str, ...]  # `gs://bucket/prefix`

    def fqns(self) -> list[str]:
        out = [f"bigquery:{s}" for s in self.sources]
        for p in self.landing_paths:
            bucket, _, prefix = p.removeprefix("gs://").partition("/")
            # VERIFY IN BUILD (VA-64): Cloud Storage FQN form; see names.gcs_fqn_matches.
            out.append(f"gcs:{bucket}.{prefix}" if prefix else f"gcs:{bucket}")
        return sorted(out)

    def declares(self, fqn: str) -> bool:
        if fqn.startswith("bigquery:"):
            return fqn.removeprefix("bigquery:") in self.sources
        if fqn.startswith("gcs:"):
            return any(gcs_fqn_matches(fqn, p) for p in self.landing_paths)
        return False


@dataclass(frozen=True)
class Outcome:
    status: str
    rule: int
    undeclared: tuple[str, ...] = ()
    age_hours: float = 0.0


def evaluate(
    *,
    created: datetime,
    now: datetime,
    resolution: Resolution,
    declared: Declared | None,
    pending_window: timedelta = timedelta(hours=48),
) -> Outcome:
    age = now - created
    age_h = round(age.total_seconds() / 3600, 2)
    if not resolution.sources and age < pending_window:
        return Outcome(PENDING, 1, age_hours=age_h)
    if not resolution.sources:
        return Outcome(INSUFFICIENT_EVIDENCE, 2, age_hours=age_h)
    if declared is not None:
        undeclared = tuple(s for s in resolution.sources if not declared.declares(s))
        if undeclared:
            return Outcome(MISMATCH, 3, undeclared, age_h)
    return Outcome(VERIFIED, 4, age_hours=age_h)
