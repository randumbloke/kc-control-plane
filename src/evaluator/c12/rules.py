"""C12 product rollup, status and freshness (ref arch §9.5; design §3.5, §5.4). Pure.

Score: 100 x rules passed / rules evaluated, pooled across the product's scanned tables,
latest job per table, rounded to two decimal places (P5-9: 0 to 100 scale).

Status, in order (ref arch §9.5):
  1 NOT_EVALUATED  no aggregate_dq_threshold or no refresh-cadence; a sensitive table has no
                   scan or publishing off; a sensitive table's latest result is missing or older
                   than the refresh period plus its tolerance; or no rule was evaluated
  2 NON_COMPLIANT  score < threshold; any critical rule failed; or freshness LATE
  3 COMPLIANT      otherwise

Freshness: a table is LATE when its BigQuery last-modified time is before the latest
scheduled refresh that is more than `thresholdInMinutes` overdue. The product is LATE if any
table is late. Daily and Hourly use `refreshTime` as the schedule; Weekly and Monthly (no day in
the aspect) use the period plus tolerance since the last modification; Irregular uses
`cronSchedule`. No refresh-cadence: freshness is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter

COMPLIANT = "COMPLIANT"
NON_COMPLIANT = "NON_COMPLIANT"
NOT_EVALUATED = "NOT_EVALUATED"
ON_TIME = "ON_TIME"
LATE = "LATE"

PERIOD_MINUTES = {"HOURLY": 60, "DAILY": 1440, "WEEKLY": 10080, "MONTHLY": 44640}


@dataclass(frozen=True)
class Cadence:
    """The `refresh-cadence` system aspect (design §3.5).

    VERIFY IN BUILD (VA-56): field names and value case as returned by the API, and that
    `refreshTime` is local time in the scheduler time zone.
    """

    frequency: str
    threshold_minutes: int
    refresh_time: str | None = None
    cron_schedule: str | None = None

    @classmethod
    def from_aspect(cls, data: dict | None) -> Cadence | None:
        if not data or "frequency" not in data:
            return None
        threshold = data.get("thresholdInMinutes", data.get("threshold_minutes"))
        if threshold is None:
            return None
        return cls(
            frequency=str(data["frequency"]).upper(),
            threshold_minutes=int(threshold),
            refresh_time=data.get("refreshTime", data.get("refresh_time")),
            cron_schedule=data.get("cronSchedule", data.get("cron_schedule")),
        )

    def cron(self) -> str | None:
        if self.cron_schedule:
            return self.cron_schedule
        hh, mm = (0, 0)
        if self.refresh_time:
            hh, mm = (int(x) for x in self.refresh_time.split(":"))
        if self.frequency == "DAILY":
            return f"{mm} {hh} * * *"
        if self.frequency == "HOURLY":
            return f"{mm} * * * *"
        return None

    def period(self, now: datetime, tz: str) -> timedelta | None:
        if self.frequency in PERIOD_MINUTES and not self.cron_schedule:
            return timedelta(minutes=PERIOD_MINUTES[self.frequency])
        expr = self.cron()
        if not expr:
            return None
        it = croniter(expr, now.astimezone(ZoneInfo(tz)))
        prev = it.get_prev(datetime)
        prev2 = it.get_prev(datetime)
        return prev - prev2


def table_is_late(last_modified: datetime, cadence: Cadence, now: datetime, tz: str) -> bool:
    tolerance = timedelta(minutes=cadence.threshold_minutes)
    expr = cadence.cron()
    if expr:
        # Latest scheduled refresh whose tolerance has run out.
        due = croniter(expr, (now - tolerance).astimezone(ZoneInfo(tz))).get_prev(datetime)
        return last_modified < due
    period = cadence.period(now, tz)
    if period is None:  # irregular without a schedule: nothing to be late against
        return False
    return now - last_modified > period + tolerance


@dataclass(frozen=True)
class RuleResult:
    name: str
    passed: bool
    critical: bool = False


@dataclass(frozen=True)
class JobResult:
    job_id: str
    end_time: datetime
    rules: tuple[RuleResult, ...]


@dataclass(frozen=True)
class ScanState:
    exists: bool
    publishing: bool = False
    exports: bool = False


@dataclass(frozen=True)
class ProductTable:
    resource: str
    sensitive: bool
    last_modified: datetime | None
    scan: ScanState
    latest_job: JobResult | None


@dataclass
class Rollup:
    status: str
    score: float | None
    freshness: str | None
    reasons: list[str] = field(default_factory=list)
    late_tables: list[str] = field(default_factory=list)
    failed_critical: list[str] = field(default_factory=list)
    rules_passed: int = 0
    rules_evaluated: int = 0


def pooled_score(tables: list[ProductTable]) -> tuple[float | None, int, int]:
    passed = evaluated = 0
    for t in tables:
        if t.latest_job is None:
            continue
        evaluated += len(t.latest_job.rules)
        passed += sum(1 for r in t.latest_job.rules if r.passed)
    if evaluated == 0:
        return None, 0, 0
    return round(100.0 * passed / evaluated, 2), passed, evaluated


def rollup(
    *,
    tables: list[ProductTable],
    threshold: float | None,
    cadence: Cadence | None,
    now: datetime,
    tz: str,
) -> Rollup:
    freshness: str | None = None
    late: list[str] = []
    if cadence is not None:
        for t in tables:
            if t.last_modified is None or table_is_late(t.last_modified, cadence, now, tz):
                late.append(t.resource)
        freshness = LATE if late else ON_TIME

    score, passed, evaluated = pooled_score(tables)
    failed_critical = sorted(
        f"{t.resource}#{r.name}"
        for t in tables
        if t.latest_job
        for r in t.latest_job.rules
        if r.critical and not r.passed
    )

    reasons: list[str] = []
    if threshold is None:
        reasons.append("no aggregate_dq_threshold")
    if cadence is None:
        reasons.append("no refresh-cadence")
    period = cadence.period(now, tz) if cadence else None
    for t in tables:
        if not t.sensitive:
            continue
        if not t.scan.exists:
            reasons.append(f"sensitive table has no scan: {t.resource}")
        elif not t.scan.publishing:
            reasons.append(f"sensitive table scan does not publish: {t.resource}")
        elif t.latest_job is None:
            reasons.append(f"sensitive table has no result: {t.resource}")
        elif (
            period is not None
            and cadence is not None
            and (
                now - t.latest_job.end_time > period + timedelta(minutes=cadence.threshold_minutes)
            )
        ):
            reasons.append(f"sensitive table result is stale: {t.resource}")
    if score is None and not reasons:
        reasons.append("no rule results for any product table")

    base = dict(
        freshness=freshness,
        late_tables=late,
        failed_critical=failed_critical,
        rules_passed=passed,
        rules_evaluated=evaluated,
    )
    if reasons:
        return Rollup(status=NOT_EVALUATED, score=None, reasons=reasons, **base)
    nc: list[str] = []
    assert score is not None and threshold is not None
    if score < threshold:
        nc.append(f"score {score} below threshold {threshold}")
    if failed_critical:
        nc.append("critical rule failed")
    if freshness == LATE:
        nc.append("freshness LATE")
    if nc:
        return Rollup(status=NON_COMPLIANT, score=score, reasons=nc, **base)
    return Rollup(status=COMPLIANT, score=score, **base)


def coverage(scan: ScanState) -> tuple[bool, str]:
    """C12 coverage of one table that needs a scan (ref arch §9.7)."""
    if not scan.exists:
        return False, "no data quality scan"
    if not scan.publishing:
        return False, "scan does not publish to the catalogue"
    if not scan.exports:
        return False, "scan does not export to the central results table"
    return True, "scan publishes and exports"
