"""Write `images.json` (schemas/images.schema.json) from pushed image digests (design §6.10)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def build(registry: str, commit: str, digests: dict[str, str]) -> dict:
    images = {}
    for name, digest in sorted(digests.items()):
        if not DIGEST.match(digest):
            raise ValueError(f"{name}: not an image digest: {digest!r}")
        images[name] = {"repository": f"{registry}/{name}", "digest": digest, "commit": commit}
    return {"images": images}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--registry", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--digest", action="append", required=True, help="name=sha256:...")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    digests = dict(d.split("=", 1) for d in a.digest)
    Path(a.out).write_text(json.dumps(build(a.registry, a.commit, digests), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
