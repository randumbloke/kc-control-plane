"""kc-validate on a domain repository: valid fixture passes; cross-file checks fail."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from kc_validate.cli import main
from kc_validate.validator import substitute_env, validate_repo

REPO = Path(__file__).parent / "fixtures" / "repos" / "dda-customer"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dst = tmp_path / "dda-customer"
    shutil.copytree(REPO, dst)
    return dst


def edit_yaml(path: Path, fn):
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_valid_repo_passes(repo):
    r = validate_repo(repo)
    assert r.ok, r.errors
    assert len(r.checked) == 4


def test_cli_exit_codes(repo, capsys):
    assert main(["repo", str(repo)]) == 0
    assert main(["schemas"]) == 0
    assert main(["file", str(repo / "datasets.yaml"), "--schema", "datasets"]) == 0
    assert main(["file", str(repo / "datasets.yaml"), "--schema", "product"]) == 1


def test_env_substitution():
    assert substitute_env({"a": ["x-{env}"], "{env}": 1}, "test") == {"a": ["x-test"], "test": 1}


def test_env_placeholder_must_resolve_to_valid_email(repo):
    edit_yaml(
        repo / "products/customer-profile.yaml",
        lambda d: d.update(owners_group="dda-{envx}-owners@x.com"),
    )
    assert not validate_repo(repo).ok


def test_asset_in_undeclared_dataset(repo):
    edit_yaml(
        repo / "products/customer-profile.yaml",
        lambda d: d["assets"]["customer_profile"].update(table="customer2_analytics.t"),
    )
    r = validate_repo(repo)
    assert any("not declared in datasets.yaml" in e for e in r.errors)


def test_asset_in_non_analytics_dataset(repo):
    # Schema forces *_analytics; the declared stage must also be analytics.
    edit_yaml(
        repo / "datasets.yaml",
        lambda d: d["datasets"].update(
            customer_analytics={"stage": "curated", "sensitivity": "sensitive"}
        ),
    )
    r = validate_repo(repo)
    assert any("name stage does not match" in e for e in r.errors)
    assert any("not an analytics dataset" in e for e in r.errors)


def test_approvers_at_most_ten(repo):
    edit_yaml(
        repo / "products/customer-profile.yaml",
        lambda d: d.update(approvers=[f"a{i}@mikegyoung.joonix.net" for i in range(11)]),
    )
    assert not validate_repo(repo).ok


def test_undeclared_access_group(repo):
    edit_yaml(
        repo / "products/customer-profile.yaml",
        lambda d: d["assets"]["customer_profile"].update(
            access={"writer": "roles/bigquery.dataEditor"}
        ),
    )
    assert any("access group 'writer'" in e for e in validate_repo(repo).errors)


def test_product_id_matches_file_name(repo):
    edit_yaml(repo / "products/customer-profile.yaml", lambda d: d.update(id="other"))
    assert any("does not match the file name" in e for e in validate_repo(repo).errors)


def test_irregular_needs_cron(repo):
    edit_yaml(
        repo / "products/customer-profile.yaml",
        lambda d: d["contract"].update(
            refresh_cadence={"frequency": "Irregular", "threshold_minutes": 60}
        ),
    )
    assert any("needs cron_schedule" in e for e in validate_repo(repo).errors)


def test_dq_path_must_match_table(repo):
    edit_yaml(
        repo / "dq/customer_curated/contacts.yaml",
        lambda d: d.update(table="customer_curated.other"),
    )
    assert any("does not match the path" in e for e in validate_repo(repo).errors)


def test_dq_dataset_must_be_declared(repo):
    (repo / "dq/customer_staging").mkdir()
    (repo / "dq/customer_staging/t.yaml").write_text(
        "table: customer_staging.t\ntrigger: on_demand\nrules:\n"
        "  - {name: r, dimension: VOLUME, template: table_condition_expectation}\n"
    )
    assert any("customer_staging is not declared" in e for e in validate_repo(repo).errors)


def test_manifest_domain_and_targets(repo):
    p = repo / "pipelines/lineage/manifest.json"
    m = json.loads(p.read_text())
    m["domain"] = "accounts-dev"
    m["targets"]["kcdda-core-dwh-dev.customer_staging.x"] = {"sources": []}
    p.write_text(json.dumps(m))
    errs = validate_repo(repo).errors
    assert any("is not customer-dev" in e for e in errs)
    assert any("customer_staging is not declared" in e for e in errs)


def test_dataset_prefix_must_match_business(repo):
    edit_yaml(
        repo / "datasets.yaml",
        lambda d: d["datasets"].update(accounts_raw={"stage": "raw", "sensitivity": "sensitive"}),
    )
    assert any("must start with customer_" in e for e in validate_repo(repo).errors)


def test_missing_datasets_file(repo):
    (repo / "datasets.yaml").unlink()
    assert any("datasets.yaml: missing" in e for e in validate_repo(repo).errors)
