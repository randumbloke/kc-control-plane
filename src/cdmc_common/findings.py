"""Findings (design §5.2) and their publication on the `findings` topic.

A finding separates the authoritative `evaluation` block, computed by deterministic code,
from the advisory `narrative` block (D-01). Narratives are never part of the published
message (P5-2): they arrive later in `cdmc_report.narratives`. In phase 1 no component
writes a narrative, so `narrative` is always None.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from cdmc_common.transport import Transport

PUBSUB = "https://pubsub.googleapis.com/v1"

FINDING_STATUSES = ("PASS", "FAIL", "PENDING", "NOT_EVALUATED")
TRIGGERS = ("EVENT", "SWEEP", "RECONCILE")
ASSET_TYPES = ("DATASET", "TABLE", "COLUMN", "DATA_PRODUCT", "DATA_DOMAIN", "BINDING", "MEMBERSHIP")

# Control status -> evaluation.status (design §5.2). One table; a golden test checks it
# against the aspect enum values of design §5.11.
CONTROL_STATUS_MAP: dict[str, dict[str, str]] = {
    "C14.lineage": {
        "VERIFIED": "PASS",
        "PENDING": "PENDING",
        "INSUFFICIENT_EVIDENCE": "FAIL",
        "MISMATCH": "FAIL",
    },
    "C12.product_status": {
        "COMPLIANT": "PASS",
        "NON_COMPLIANT": "FAIL",
        "NOT_EVALUATED": "FAIL",
    },
    "C12.freshness": {"ON_TIME": "PASS", "LATE": "FAIL"},
}


def map_status(check: str, control_status: str) -> str:
    return CONTROL_STATUS_MAP[check][control_status]


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def finding_id(asset: str, check: str, trigger_id: str, evaluator_version: str) -> str:
    """Idempotency key (design §2.5.2): redelivery of one event gives one key."""
    raw = "\x1f".join([asset, check, trigger_id, evaluator_version])
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Evaluation:
    status: str
    evaluator_version: str
    evaluated_at: str
    source_ref: str
    trigger: str
    measured_value: float | None = None
    threshold: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in FINDING_STATUSES:
            raise ValueError(f"bad evaluation.status {self.status}")
        if self.trigger not in TRIGGERS:
            raise ValueError(f"bad evaluation.trigger {self.trigger}")


@dataclass(frozen=True)
class Route:
    owner: str | None = None
    steward: str | None = None
    escalation: str | None = None


@dataclass(frozen=True)
class Narrative:
    summary: str
    model: str
    prompt_version: str
    authoritative: bool = False


@dataclass(frozen=True)
class Finding:
    finding_id: str
    environment: str
    control: str
    check: str
    asset: str
    asset_type: str
    evaluation: Evaluation
    domain: str | None = None
    product: str | None = None
    detected_at: str = ""
    route: Route = field(default_factory=Route)
    narrative: Narrative | None = None  # D-01: advisory only; always None in phase 1

    def to_message(self) -> dict[str, Any]:
        """The `cdmc-finding-v1` message body (design §5.2). No narrative (P5-2).

        `evaluation.detail` is a JSON string. VERIFY IN BUILD (VA-61): the BigQuery
        subscription writes it to a JSON column; fallback STRING + PARSE_JSON in the views.
        """
        ev = self.evaluation
        return {
            "finding_id": self.finding_id,
            "environment": self.environment,
            "control": self.control,
            "check": self.check,
            "asset": self.asset,
            "asset_type": self.asset_type,
            "domain": self.domain,
            "product": self.product,
            "detected_at": self.detected_at,
            "evaluation": {
                "status": ev.status,
                "measured_value": ev.measured_value,
                "threshold": ev.threshold,
                "detail": json.dumps(ev.detail, sort_keys=True),
                "evaluator_version": ev.evaluator_version,
                "evaluated_at": ev.evaluated_at,
                "source_ref": ev.source_ref,
                "trigger": ev.trigger,
            },
            "route": {
                "owner": self.route.owner,
                "steward": self.route.steward,
                "escalation": self.route.escalation,
            },
        }

    def to_record(self) -> dict[str, Any]:
        """Message plus the (empty) advisory block, for logs and golden fixtures."""
        rec = self.to_message()
        rec["evaluation"]["detail"] = self.evaluation.detail
        rec["narrative"] = None if self.narrative is None else vars(self.narrative)
        return rec


def make_finding(
    *,
    env: str,
    control: str,
    check: str,
    asset: str,
    asset_type: str,
    status: str,
    evaluator_version: str,
    trigger: str,
    trigger_id: str,
    source_ref: str,
    now: datetime,
    domain: str | None = None,
    product: str | None = None,
    measured_value: float | None = None,
    threshold: float | None = None,
    detail: dict[str, Any] | None = None,
    route: Route | None = None,
) -> Finding:
    if asset_type not in ASSET_TYPES:
        raise ValueError(asset_type)
    return Finding(
        finding_id=finding_id(asset, check, trigger_id, evaluator_version),
        environment=env,
        control=control,
        check=check,
        asset=asset,
        asset_type=asset_type,
        domain=domain,
        product=product,
        detected_at=iso(now),
        evaluation=Evaluation(
            status=status,
            evaluator_version=evaluator_version,
            evaluated_at=iso(now),
            source_ref=source_ref,
            trigger=trigger,
            measured_value=measured_value,
            threshold=threshold,
            detail=detail or {},
        ),
        route=route or Route(),
    )


class FindingsPublisher(Protocol):
    def publish(self, finding: Finding) -> None: ...


class PubSubFindingsPublisher:
    def __init__(self, transport: Transport, topic: str):
        self._t = transport
        self._topic = topic

    def publish(self, finding: Finding) -> None:
        data = json.dumps(finding.to_message()).encode()
        self._t.post(
            f"{PUBSUB}/{self._topic}:publish",
            json={
                "messages": [
                    {
                        "data": base64.b64encode(data).decode(),
                        "attributes": {
                            "control": finding.control,
                            "check": finding.check,
                            "status": finding.evaluation.status,
                        },
                    }
                ]
            },
        )
