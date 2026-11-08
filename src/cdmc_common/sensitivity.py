"""The one sensitivity test, `product_is_sensitive` (design §4.2.2, P5-8c).

A table is sensitive when its dataset carries `cdmc-sensitivity=sensitive` or it has a
`CONFIDENTIAL` or `RESTRICTED` column; a product is sensitive when any asset (or a view
asset's base table) is. Column levels come from `cdmc-classification`, written by the
classifier from phase 2; in phase 1 the dataset label alone decides, and `column_levels`
is empty.
"""

from __future__ import annotations

from collections.abc import Iterable

SENSITIVE_LEVELS = frozenset({"CONFIDENTIAL", "RESTRICTED"})


def table_is_sensitive(
    dataset_labels: dict[str, str] | None, column_levels: Iterable[str] = ()
) -> bool:
    if (dataset_labels or {}).get("cdmc-sensitivity") == "sensitive":
        return True
    return any(level in SENSITIVE_LEVELS for level in column_levels)


def product_is_sensitive(tables: Iterable[bool]) -> bool:
    """True when any of the product's tables (assets and view base tables) is sensitive."""
    return any(tables)
