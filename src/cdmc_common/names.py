"""Resource name helpers: BigQuery table resources, catalogue entry names, lineage FQNs,
domain IDs and pipeline identities (design §1.6, §2.3.2, §5.4.1, §6.7)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from cdmc_common.config import ENV_TO_TIER

_TABLE_RE = re.compile(
    r"^(?://bigquery\.googleapis\.com/)?projects/(?P<p>[^/]+)/datasets/(?P<d>[^/]+)/tables/(?P<t>[^/]+)$"
)
_DOMAIN_RE = re.compile(r"^(?P<business>[a-z][a-z0-9]*)-(?P<env>dev|test|uat|prod)$")


@dataclass(frozen=True, order=True)
class TableRef:
    project: str
    dataset: str
    table: str

    @classmethod
    def parse(cls, value: str) -> TableRef:
        """Accept a full resource name, a relative name, `bigquery:p.d.t` or `p.d.t`."""
        m = _TABLE_RE.match(value)
        if m:
            return cls(m["p"], m["d"], m["t"])
        v = value.removeprefix("bigquery:")
        parts = v.split(".")
        if len(parts) == 3 and all(parts):
            return cls(*parts)
        raise ValueError(f"not a BigQuery table reference: {value!r}")

    @property
    def resource(self) -> str:
        return (
            f"//bigquery.googleapis.com/projects/{self.project}"
            f"/datasets/{self.dataset}/tables/{self.table}"
        )

    @property
    def dataset_resource(self) -> str:
        return f"//bigquery.googleapis.com/projects/{self.project}/datasets/{self.dataset}"

    @property
    def fqn(self) -> str:
        """Data Lineage fully qualified name (design §5.11.4)."""
        return f"bigquery:{self.project}.{self.dataset}.{self.table}"

    @property
    def dotted(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    def entry_name(self, location: str) -> str:
        """Catalogue entry of the table in the `@bigquery` system entry group.

        VERIFY IN BUILD (VA-76): entry location equals the dataset location
        (`australia-southeast1` in phase 1).
        """
        return (
            f"projects/{self.project}/locations/{location}/entryGroups/@bigquery/entries/"
            f"bigquery.googleapis.com/projects/{self.project}/datasets/{self.dataset}"
            f"/tables/{self.table}"
        )

    @classmethod
    def from_entry_name(cls, entry_name: str) -> TableRef | None:
        marker = "/entries/bigquery.googleapis.com/"
        if marker not in entry_name:
            return None
        try:
            return cls.parse(entry_name.split(marker, 1)[1])
        except ValueError:
            return None


def parse_domain(domain_id: str) -> tuple[str, str]:
    """`customer-dev` -> (`customer`, `dev`)."""
    m = _DOMAIN_RE.match(domain_id)
    if not m:
        raise ValueError(f"not a data domain ID: {domain_id!r}")
    return m["business"], m["env"]


def domain_pipeline_identities(
    domain_id: str, cicd_project_id: str, single_deployer: bool
) -> set[str]:
    """Identities allowed to upload a domain's state-bucket objects (design §2.3.2, §6.7.2)."""
    business, env = parse_domain(domain_id)
    tier = ENV_TO_TIER[env]
    allowed = {f"tf-{business}-{tier}@{cicd_project_id}.iam.gserviceaccount.com"}
    if single_deployer:
        allowed.add(f"tf-deployer@{cicd_project_id}.iam.gserviceaccount.com")
    return allowed


def gcs_fqn_matches(fqn: str, landing_path: str) -> bool:
    """True when a run-time lineage FQN for Cloud Storage falls under a declared landing path.

    VERIFY IN BUILD (VA-64): the exact Cloud Storage FQN form recorded by BigQuery load jobs.
    Both `gcs:<bucket>.<path>` and `gcs:<bucket>/<path>` are accepted.
    """
    if not fqn.startswith("gcs:") or not landing_path.startswith("gs://"):
        return False
    rest = landing_path.removeprefix("gs://")
    bucket, _, prefix = rest.partition("/")
    body = fqn.removeprefix("gcs:")
    if not prefix:
        return body == bucket or body.startswith((f"{bucket}.", f"{bucket}/"))
    return body.startswith((f"{bucket}.{prefix}", f"{bucket}/{prefix}"))


def product_resource(project: str, location: str, product_id: str) -> str:
    """Relative resource name of a data product (Dataplex v1 `dataProducts`)."""
    return f"projects/{project}/locations/{location}/dataProducts/{product_id}"
