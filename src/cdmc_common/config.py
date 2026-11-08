"""Runtime settings for the control plane, read from environment variables.

Every value is set by the `cdmc-governance` Terraform that deploys the service and the job
(design §2.5, §5.1, §6.16). Defaults are the phase 1 reference estate (deployment/contracts).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None, default: list[str]) -> list[str]:
    if value is None or value.strip() == "":
        return list(default)
    return [v.strip() for v in value.split(",") if v.strip()]


ENV_TO_TIER = {"dev": "nonprod", "test": "nonprod", "uat": "prod", "prod": "prod"}


@dataclass(frozen=True)
class Settings:
    """Deployment parameters of one evaluator deployment (one hub, one environment)."""

    env: str = "dev"
    hub_project_id: str = "kcdda-dgov-hub-nonprod"
    region: str = "australia-southeast1"
    cicd_project_id: str = "pltfrm-ngnrng"
    hub_state_bucket: str = "kcdda-dgov-hub-nonprod-dda-state"
    findings_topic: str = "projects/kcdda-dgov-hub-nonprod/topics/findings"
    dq_results_table: str = "kcdda-dgov-hub-nonprod.dq_results.scan_results"
    rollup_queue: str = (
        "projects/kcdda-dgov-hub-nonprod/locations/australia-southeast1/queues/product-rollups"
    )
    rollup_handler_url: str = ""
    rollup_invoker_sa: str = ""
    rollup_audience: str = ""  # optional; default is the base URL of ROLLUP_HANDLER_URL
    rollup_window_seconds: int = 300
    data_projects: list[str] = field(
        default_factory=lambda: [
            "kcdda-core-dwh-dev",
            "kcdda-customer-analytics-dev",
            "kcdda-accounts-analytics-dev",
        ]
    )
    single_deployer: bool = True
    scheduler_time_zone: str = "Australia/Sydney"
    evaluator_modules: list[str] = field(default_factory=lambda: ["c12", "c14"])
    lineage_max_hops: int = 20
    # 48 h outer bound for C14 PENDING (design §5.4.1). VERIFY IN BUILD (VA-22): lineage is
    # available within 24 hours; VA-64: it usually appears within minutes.
    pending_window_hours: int = 48

    @property
    def tier(self) -> str:
        return ENV_TO_TIER[self.env]

    def aspect_key(self, aspect_type: str) -> str:
        """Aspect key of a custom aspect type in the hub (design §5.11).

        VERIFY IN BUILD (VA-76): that writes accept the key with the hub project ID (not its
        number); readers match on the `.global.<type>` suffix for that reason.
        """
        return f"{self.hub_project_id}.global.{aspect_type}"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        e = os.environ if environ is None else environ
        d = cls()
        hub = e.get("HUB_PROJECT_ID", d.hub_project_id)
        region = e.get("REGION", d.region)
        return cls(
            env=e.get("DDA_ENV", d.env),
            hub_project_id=hub,
            region=region,
            cicd_project_id=e.get("CICD_PROJECT_ID", d.cicd_project_id),
            hub_state_bucket=e.get("HUB_STATE_BUCKET", f"{hub}-dda-state"),
            findings_topic=e.get("FINDINGS_TOPIC", f"projects/{hub}/topics/findings"),
            dq_results_table=e.get("DQ_RESULTS_TABLE", f"{hub}.dq_results.scan_results"),
            rollup_queue=e.get(
                "ROLLUP_QUEUE", f"projects/{hub}/locations/{region}/queues/product-rollups"
            ),
            rollup_handler_url=e.get("ROLLUP_HANDLER_URL", ""),
            rollup_invoker_sa=e.get("ROLLUP_INVOKER_SA", ""),
            rollup_audience=e.get("ROLLUP_AUDIENCE", ""),
            rollup_window_seconds=int(e.get("ROLLUP_WINDOW_SECONDS", "300")),
            data_projects=_list(e.get("DATA_PROJECTS"), d.data_projects),
            single_deployer=_bool(e.get("SINGLE_DEPLOYER"), d.single_deployer),
            scheduler_time_zone=e.get("SCHEDULER_TIME_ZONE", d.scheduler_time_zone),
            evaluator_modules=_list(e.get("EVALUATOR_MODULES"), d.evaluator_modules),
        )
