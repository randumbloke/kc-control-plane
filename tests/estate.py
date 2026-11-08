"""The phase 1 reference estate as fixture data (deployment/contracts §2)."""

from __future__ import annotations

HUB = "kcdda-dgov-hub-nonprod"
LOC = "australia-southeast1"
CORE = "kcdda-core-dwh-dev"
CUST_AN = "kcdda-customer-analytics-dev"
ACCT_AN = "kcdda-accounts-analytics-dev"
BUCKET = f"{HUB}-dda-state"
DEPLOYER = "tf-deployer@pltfrm-ngnrng.iam.gserviceaccount.com"

CUSTOMER_PROFILE_ENTRY = (
    f"projects/{CUST_AN}/locations/{LOC}/entryGroups/@dataplex/entries/customer-profile"
)


def dom(name: str, parent: str | None = None) -> dict:
    d = {"name": f"projects/{HUB}/locations/{LOC}/dataDomains/{name}"}
    if parent:
        d["parentDataDomain"] = f"projects/{HUB}/locations/{LOC}/dataDomains/{parent}"
    return d


def domains_and_bindings() -> tuple[list[dict], dict[str, list[dict]]]:
    domains = [dom("customer-dev"), dom("accounts-dev")]
    bindings: dict[str, list[dict]] = {}
    layout = {
        "customer-dev": [("raw", CORE, "customer_raw"), ("curated", CORE, "customer_curated"),
                         ("analytics", CUST_AN, "customer_analytics")],
        "accounts-dev": [("staging", CORE, "accounts_staging"),
                         ("curated", CORE, "accounts_curated"),
                         ("analytics", ACCT_AN, "accounts_analytics")],
    }
    for d, subs in layout.items():
        business = d.split("-")[0]
        for stage, project, ds in subs:
            sub = f"{business}-{stage}"  # VERIFY IN BUILD: subdomain IDs (contracts §2)
            domains.append(dom(sub, d))
            bindings[f"projects/{HUB}/locations/{LOC}/dataDomains/{sub}"] = [
                {"name": f"b-{ds}",
                 "resource": f"//bigquery.googleapis.com/projects/{project}/datasets/{ds}"}
            ]
    return domains, bindings


def published_customer_profile(**overrides) -> dict:
    data = {
        "env": "dev",
        "domain": "customer-dev",
        "product_id": "customer-profile",
        "product_name": "Customer Profile",
        "dataplex_entry": CUSTOMER_PROFILE_ENTRY,
        "steward_group": "dda-customer-dev-stewards@mikegyoung.joonix.net",
        "owners_group": "dda-dp-customer-profile-dev-owners@mikegyoung.joonix.net",
        "aggregate_dq_threshold": 95.0,
        "lifecycle": "published",
        "assets": {
            "customer_profile": {
                "table_resource": f"//bigquery.googleapis.com/projects/{CUST_AN}/datasets/customer_analytics/tables/customer_profile",
                "form": "pipeline_table",
            }
        },
        "refresh_cadence": {"frequency": "Daily", "refresh_time": "06:00", "threshold_minutes": 120},
        "published_at": "2026-11-09T00:00:00Z",
        "commit": "abc123",
    }
    data.update(overrides)
    return data
