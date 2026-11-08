"""Custom aspect templates (design §5.11) and payload validation.

One YAML source, `aspect_types/cdmc_aspect_types.yaml`, holds the five templates. Every writer
validates its payload against it before writing.
"""

from __future__ import annotations

import re
from functools import cache
from importlib import resources
from typing import Any

import yaml

SCHEMA_VERSION = 1
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


class AspectValidationError(ValueError):
    pass


@cache
def templates() -> dict[str, dict[str, Any]]:
    text = resources.files("cdmc_common").joinpath("aspect_types/cdmc_aspect_types.yaml")
    doc = yaml.safe_load(text.read_text())
    return {k: v["metadataTemplate"] for k, v in doc["aspect_types"].items()}


def enum_values(aspect_type: str, field: str) -> list[str]:
    for f in templates()[aspect_type]["recordFields"]:
        if f["name"] == field:
            return [v["name"] for v in f["enumValues"]]
    raise KeyError(field)


def _check(field: dict[str, Any], value: Any, path: str) -> None:
    t = field["type"]
    ok = {
        "string": lambda v: isinstance(v, str),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "double": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "bool": lambda v: isinstance(v, bool),
        "datetime": lambda v: isinstance(v, str) and bool(_DATETIME.match(v)),
        "enum": lambda v: v in [e["name"] for e in field.get("enumValues", [])],
        "array": lambda v: isinstance(v, list),
        "record": lambda v: isinstance(v, dict),
    }[t]
    if not ok(value):
        raise AspectValidationError(f"{path}: {value!r} is not a valid {t}")
    if t == "array":
        for i, item in enumerate(value):
            _check(field["arrayItems"], item, f"{path}[{i}]")
    if t == "record":
        _check_record(field["recordFields"], value, path)


def _check_record(fields: list[dict[str, Any]], data: dict[str, Any], path: str) -> None:
    known = {f["name"]: f for f in fields}
    for name in data:
        if name not in known:
            raise AspectValidationError(f"{path}.{name}: unknown field")
    for f in fields:
        name = f["name"]
        if name not in data or data[name] is None:
            if (f.get("constraints") or {}).get("required"):
                raise AspectValidationError(f"{path}.{name}: required field missing")
            if name in data:
                raise AspectValidationError(f"{path}.{name}: omit optional fields, do not null")
            continue
        _check(f, data[name], f"{path}.{name}")


def validate(aspect_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Raise AspectValidationError unless `data` matches the template; return `data`."""
    _check_record(templates()[aspect_type]["recordFields"], data, aspect_type)
    return data


def evaluation_record(evaluator_version: str, evaluated_at: str, source_ref: str) -> dict[str, str]:
    return {
        "evaluator_version": evaluator_version,
        "evaluated_at": evaluated_at,
        "source_ref": source_ref,
    }
