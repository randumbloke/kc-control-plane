"""Build outputs: images.json matches the images schema; build files parse."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from kc_validate import schemas

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wij", ROOT / "build" / "write_images_json.py")
wij = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wij)


def test_images_json_matches_schema(tmp_path):
    out = tmp_path / "images.json"
    reg = "australia-southeast1-docker.pkg.dev/pltfrm-ngnrng/dda-control-plane"
    wij.main(
        [
            "--registry",
            reg,
            "--commit",
            "abc",
            "--digest",
            "evaluator=sha256:" + "1" * 64,
            "--digest",
            "evaluator-sweep=sha256:" + "2" * 64,
            "--out",
            str(out),
        ]
    )
    data = json.loads(out.read_text())
    assert schemas.errors("images", data) == []
    assert data["images"]["evaluator"]["repository"] == f"{reg}/evaluator"


def test_bad_digest_rejected():
    with pytest.raises(ValueError):
        wij.build("r", "c", {"evaluator": "latest"})


def test_cloudbuild_and_ci_parse():
    cb = yaml.safe_load((ROOT / "build" / "cloudbuild.yaml").read_text())
    ids = [s["id"] for s in cb["steps"]]
    assert ids == ["checks", "build-service", "build-job", "push", "images-json", "publish"]
    assert cb["substitutions"]["_STATE_BUCKET"] == "tfstate-pltfrm-ngnrng"
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert "ci-checks" in ci["jobs"]
