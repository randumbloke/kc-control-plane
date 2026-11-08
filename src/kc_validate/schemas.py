"""Loader for the canonical JSON Schemas in `schemas/` (deployment/contracts §4)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_NAMES = (
    "bindings",
    "datasets",
    "domains_state",
    "dq_spec",
    "estate",
    "images",
    "lineage_manifest",
    "outputs_domain",
    "outputs_governance_env",
    "outputs_governance_tier",
    "outputs_platform_env",
    "outputs_platform_tier",
    "product",
    "published_product",
)


def schema_dir() -> Path:
    packaged = Path(__file__).parent / "schemas"  # wheel: force-included
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas"  # source tree / editable install


@cache
def load(name: str) -> dict[str, Any]:
    return json.loads((schema_dir() / f"{name}.schema.json").read_text())


@cache
def validator(name: str) -> Draft202012Validator:
    schema = load(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def errors(name: str, instance: Any) -> list[str]:
    out = []
    for e in sorted(validator(name).iter_errors(instance), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
        out.append(f"{loc}: {e.message}")
    return out
