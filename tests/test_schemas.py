"""Canonical JSON Schemas: each compiles, valid fixtures pass, invalid variants fail."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from kc_validate import schemas

FIX = Path(__file__).parent / "fixtures" / "schemas"
CONTRACTS = Path("/Users/mikegyoung/CodeRepos/cdmc_kc/deployment/contracts/schemas")


def valid(name: str) -> dict:
    return json.loads((FIX / f"{name}.valid.json").read_text())


@pytest.mark.parametrize("name", schemas.SCHEMA_NAMES)
def test_schema_compiles(name):
    schemas.validator(name)


@pytest.mark.parametrize("path", sorted(FIX.glob("*.valid.json")), ids=lambda p: p.stem)
def test_valid_fixture(path):
    name = path.name.removesuffix(".valid.json")
    assert schemas.errors(name, json.loads(path.read_text())) == []


def _set(d, path, value):
    *head, last = path
    for p in head:
        d = d[p]
    if value is _DEL:
        del d[last]
    else:
        d[last] = value


_DEL = object()

INVALID = [
    ("datasets", ["datasets", "customer_raw", "stage"], "bronze"),
    ("datasets", ["datasets", "customer_gold"], {"stage": "raw", "sensitivity": "sensitive"}),
    ("datasets", ["datasets", "customer_raw", "sensitivity"], "secret"),
    ("product", ["id"], "Customer_Profile"),
    ("product", ["approvers"], [f"a{i}@mikegyoung.joonix.net" for i in range(11)]),
    ("product", ["assets", "customer_profile", "table"], "customer_curated.contacts"),
    ("product", ["assets", "customer_profile", "form"], "view"),
    ("product", ["contract", "aggregate_dq_threshold"], 101),
    ("product", ["contract", "refresh_cadence", "frequency"], "daily"),
    ("product", ["owners_group"], _DEL),
    ("product", ["access_groups"], {}),
    ("dq_spec", ["trigger"], {"schedule": "0 7 * * *"}),
    ("dq_spec", ["rules", 0, "dimension"], "TIMELINESS"),
    ("dq_spec", ["rules", 0, "threshold"], 1.5),
    ("dq_spec", ["rules"], []),
    ("lineage_manifest", ["version"], 2),
    ("lineage_manifest", ["domain"], "customer"),
    ("lineage_manifest", ["targets", "customer_raw.contacts"], {"sources": []}),
    ("published_product", ["assets", "customer_profile", "table_resource"], "p.d.t"),
    ("published_product", ["aggregate_dq_threshold"], -1),
    ("images", ["images", "evaluator", "digest"], "sha256:abc"),
    ("bindings", ["bindings", "customer-curated"], {"domain_name": "x", "resource": "//x"}),
    ("domains_state", ["domains", "customer-dev", "allowed_principals"], ["someone@x.com"]),
    ("outputs_domain", ["products", "customer-profile", "dataplex_entry"], _DEL),
]


@pytest.mark.parametrize(
    "name,path,value", INVALID, ids=[f"{n}:{'.'.join(map(str, p))}" for n, p, _ in INVALID]
)
def test_invalid_variant(name, path, value):
    data = copy.deepcopy(valid(name))
    _set(data, path, value)
    assert schemas.errors(name, data), f"{name} accepted {path}={value!r}"


@pytest.mark.skipif(not CONTRACTS.is_dir(), reason="spec repository not checked out beside")
def test_schemas_match_contracts_copy():
    """`schemas/` is the canonical home; it must equal the contracts source it came from."""
    for name in schemas.SCHEMA_NAMES:
        ours = json.loads((schemas.schema_dir() / f"{name}.schema.json").read_text())
        theirs = json.loads((CONTRACTS / f"{name}.schema.json").read_text())
        assert ours == theirs, name
