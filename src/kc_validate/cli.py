"""`kc-validate`: validate a domain repository, one file, or the schemas themselves.

kc-validate repo [PATH] [--env dev] [--business customer]
kc-validate file PATH --schema product [--env dev]
kc-validate schemas
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kc_validate import schemas
from kc_validate.validator import Report, check_schemas, validate_file, validate_repo


def _print(report: Report) -> int:
    for c in report.checked:
        print(f"checked  {c}")
    for e in report.errors:
        print(f"ERROR    {e}", file=sys.stderr)
    print(f"{len(report.checked)} checked, {len(report.errors)} error(s)")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="kc-validate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("repo", help="validate a domain repository")
    r.add_argument("path", nargs="?", default=".")
    r.add_argument("--env", default="dev", choices=["dev", "test", "uat", "prod"])
    r.add_argument("--business", help="business name; default from the dda-<business> folder")
    f = sub.add_parser("file", help="validate one file against one schema")
    f.add_argument("path")
    f.add_argument("--schema", required=True, choices=schemas.SCHEMA_NAMES)
    f.add_argument("--env", default="dev", choices=["dev", "test", "uat", "prod"])
    sub.add_parser("schemas", help="check that every schema is a valid JSON Schema")
    a = p.parse_args(argv)
    if a.cmd == "repo":
        return _print(validate_repo(Path(a.path), a.env, a.business))
    if a.cmd == "file":
        return _print(validate_file(Path(a.path), a.schema, a.env))
    return _print(check_schemas())


if __name__ == "__main__":
    sys.exit(main())
