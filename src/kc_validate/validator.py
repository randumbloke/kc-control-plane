"""Validate a domain repository's declarative files (deployment/contracts §4, design §3).

Files: `datasets.yaml`, `products/<id>.yaml`, `dq/<dataset>/<table>.yaml` and
`pipelines/lineage/manifest.json`. `{env}` in any string is replaced by the environment
before validation (§3.1). Schema validation is followed by cross-file checks that a JSON
Schema cannot express.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kc_validate import schemas

MAX_APPROVERS = 10  # access_approval_config.approver_emails (design §3.2)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")


def substitute_env(value: Any, env: str) -> Any:
    if isinstance(value, str):
        return value.replace("{env}", env)
    if isinstance(value, list):
        return [substitute_env(v, env) for v in value]
    if isinstance(value, dict):
        return {substitute_env(k, env): substitute_env(v, env) for k, v in value.items()}
    return value


def _load(path: Path) -> Any:
    text = path.read_text()
    return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)


_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def placeholders(value: Any, path: str = "") -> list[str]:
    """Placeholders left after `{env}` substitution (only `{env}` is defined, §3.1)."""
    if isinstance(value, str):
        return [f"{path or '(root)'}: unknown placeholder {m}" for m in _PLACEHOLDER.findall(value)]
    items = (
        value.items()
        if isinstance(value, dict)
        else enumerate(value)
        if isinstance(value, list)
        else []
    )
    out: list[str] = []
    for k, v in items:
        out += placeholders(v, f"{path}/{k}" if path else str(k))
    return out


def _schema(report: Report, rel: str, name: str, data: Any) -> bool:
    report.checked.append(rel)
    errs = schemas.errors(name, data) + placeholders(data)
    for e in errs:
        report.add(rel, e)
    return not errs


def check_datasets(report: Report, rel: str, data: dict, business: str | None) -> None:
    for name, ds in data.get("datasets", {}).items():
        if not name.endswith(f"_{ds['stage']}"):
            report.add(rel, f"dataset {name}: name stage does not match stage {ds['stage']!r}")
        if business and not name.startswith(f"{business}_"):
            report.add(rel, f"dataset {name}: name must start with {business}_")
    declared = set(data.get("datasets", {}))
    for table in data.get("table_contacts", {}) or {}:
        if table.split(".")[0] not in declared:
            report.add(rel, f"table_contacts {table}: dataset is not declared in datasets")


def check_product(
    report: Report, rel: str, data: dict, stem: str, datasets: dict[str, Any] | None
) -> None:
    if data["id"] != stem:
        report.add(rel, f"id {data['id']!r} does not match the file name {stem!r}")
    if len(data.get("approvers", [])) > MAX_APPROVERS:
        report.add(rel, f"approvers: at most {MAX_APPROVERS} (access_approval_config)")
    groups = set(data["access_groups"])
    for key, asset in data["assets"].items():
        dataset = asset["table"].split(".")[0]
        if datasets is not None:
            ds = datasets.get(dataset)
            if ds is None:
                report.add(rel, f"assets.{key}: dataset {dataset} is not declared in datasets.yaml")
            elif ds["stage"] != "analytics":
                report.add(rel, f"assets.{key}: dataset {dataset} is not an analytics dataset")
        for group in asset["access"]:
            if group not in groups:
                report.add(rel, f"assets.{key}.access: access group {group!r} is not declared")
    tables = [a["table"] for a in data["assets"].values()]
    if len(tables) != len(set(tables)):
        report.add(rel, "assets: the same table is listed twice")
    cadence = data["contract"]["refresh_cadence"]
    if cadence["frequency"] == "Irregular" and not cadence.get("cron_schedule"):
        report.add(rel, "contract.refresh_cadence: Irregular needs cron_schedule")
    if cadence["frequency"] in {"Daily", "Weekly", "Monthly"} and not cadence.get("refresh_time"):
        report.add(rel, f"contract.refresh_cadence: {cadence['frequency']} needs refresh_time")


def check_dq(
    report: Report,
    rel: str,
    data: dict,
    dataset_dir: str,
    table_stem: str,
    datasets: dict[str, Any] | None,
) -> None:
    expected = f"{dataset_dir}.{table_stem}"
    if data["table"] != expected:
        report.add(rel, f"table {data['table']!r} does not match the path ({expected})")
    if datasets is not None and dataset_dir not in datasets:
        report.add(rel, f"dataset {dataset_dir} is not declared in datasets.yaml")
    names = [r["name"] for r in data["rules"]]
    if len(names) != len(set(names)):
        report.add(rel, "rules: rule names must be unique")
    for r in data["rules"]:
        if (
            r["template"]
            not in {"table_condition_expectation", "sql_assertion", "statistic_range_expectation"}
            and "column" not in r
        ):
            report.add(rel, f"rules.{r['name']}: template {r['template']} needs a column")


def check_manifest(
    report: Report,
    rel: str,
    data: dict,
    business: str | None,
    env: str,
    datasets: dict[str, Any] | None,
) -> None:
    if business and data["domain"] != f"{business}-{env}":
        report.add(rel, f"domain {data['domain']!r} is not {business}-{env}")
    if datasets is not None:
        for target in data["targets"]:
            ds = target.split(".")[1]
            if ds not in datasets:
                report.add(rel, f"target {target}: dataset {ds} is not declared in datasets.yaml")


def validate_repo(root: Path, env: str = "dev", business: str | None = None) -> Report:
    report = Report()
    if business is None and root.resolve().name.startswith("dda-"):
        business = root.resolve().name.removeprefix("dda-")

    datasets: dict[str, Any] | None = None
    ds_path = root / "datasets.yaml"
    if ds_path.exists():
        data = substitute_env(_load(ds_path), env)
        if _schema(report, "datasets.yaml", "datasets", data):
            check_datasets(report, "datasets.yaml", data, business)
            datasets = data["datasets"]
    else:
        report.add("datasets.yaml", "missing")

    for path in sorted((root / "products").glob("*.yaml")) + sorted(
        (root / "products").glob("*.yml")
    ):
        rel = str(path.relative_to(root))
        data = substitute_env(_load(path), env)
        if _schema(report, rel, "product", data):
            check_product(report, rel, data, path.stem, datasets)

    for path in sorted((root / "dq").glob("*/*.yaml")):
        rel = str(path.relative_to(root))
        data = substitute_env(_load(path), env)
        if _schema(report, rel, "dq_spec", data):
            check_dq(report, rel, data, path.parent.name, path.stem, datasets)

    manifest = root / "pipelines" / "lineage" / "manifest.json"
    if manifest.exists():
        rel = str(manifest.relative_to(root))
        data = substitute_env(_load(manifest), env)
        if _schema(report, rel, "lineage_manifest", data):
            check_manifest(report, rel, data, business, env, datasets)
    return report


def validate_file(path: Path, schema: str, env: str = "dev") -> Report:
    report = Report()
    _schema(report, str(path), schema, substitute_env(_load(path), env))
    return report


def check_schemas() -> Report:
    """Every canonical schema is itself a valid draft 2020-12 schema."""
    report = Report()
    for name in schemas.SCHEMA_NAMES:
        try:
            schemas.validator(name)
            report.checked.append(f"{name}.schema.json")
        except Exception as e:  # noqa: BLE001
            report.add(f"{name}.schema.json", str(e))
    return report
