"""Request handling for the evaluator service and job, independent of the web framework."""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from cdmc_common.findings import make_finding
from cdmc_common.names import TableRef
from cdmc_common.products import parse_lineage_path
from evaluator.c12.module import C12Module
from evaluator.c14.module import C14Module
from evaluator.contract_writer import ContractWriter
from evaluator.deps import Deps
from evaluator.uploader import StateUpload, parse_audit_event, uploader_allowed
from kc_validate import schemas

log = logging.getLogger(__name__)
_SEG = r"[^/\s\"]+"
ENTRY_RE = re.compile(rf"projects/{_SEG}/locations/{_SEG}/entryGroups/{_SEG}/entries/[^\s\"]+")
MANIFEST_VERSION = "c14-manifest-source@1.0.0"


@dataclass
class HandlerResult:
    actions: list[str] = field(default_factory=list)
    findings: int = 0


def extract_entry_name(payload: Any) -> str | None:
    """First catalogue entry name anywhere in a metadata feed payload.

    VERIFY IN BUILD (VA-09): the feed's event payload shape. Searching the payload for the
    entry name keeps the handler independent of the field that carries it.
    """
    text = json.dumps(payload) if not isinstance(payload, str) else payload
    m = ENTRY_RE.search(text)
    return m.group(0) if m else None


def decode_push(envelope: dict[str, Any]) -> tuple[Any, str]:
    msg = envelope.get("message") or {}
    raw = msg.get("data")
    data: Any = {}
    if raw:
        decoded = base64.b64decode(raw).decode("utf-8", "replace")
        try:
            data = json.loads(decoded)
        except json.JSONDecodeError:
            data = decoded
    payload = {"data": data, "attributes": msg.get("attributes") or {}}
    return payload, msg.get("messageId") or msg.get("message_id") or ""


class Evaluator:
    """Runs the enabled modules (`evaluator_modules`, design §6.17) for one asset."""

    def __init__(self, deps: Deps):
        self.d = deps
        mods = set(deps.settings.evaluator_modules)
        self.c14 = C14Module(deps) if "c14" in mods else None
        self.c12 = C12Module(deps) if "c12" in mods else None

    def evaluate_table(self, ref: TableRef, trigger: str, trigger_id: str) -> HandlerResult:
        r = HandlerResult()
        domain = self.d.domain_index.get(ref.dataset_resource)
        if self.c14 and self.c14.evaluate(ref, trigger, trigger_id) is not None:
            r.actions.append(f"c14:{ref.dotted}")
            r.findings += 1
        info = self.d.bigquery.get_table(ref) if self.c12 else None
        # Only BigQuery tables can be scanned; views are measured on their base tables (§3.4).
        if (
            self.c12
            and info is not None
            and info.table_type == "TABLE"
            and (self.c12.table_is_sensitive(ref))
        ):
            self.c12.coverage(ref, trigger, trigger_id, domain)
            r.actions.append(f"c12.coverage:{ref.dotted}")
            r.findings += 1
        return r

    def rollup(self, product_key: str, trigger: str, trigger_id: str) -> HandlerResult:
        r = HandlerResult()
        if not self.c12:
            return r
        product = self.d.registry.load_one(product_key)
        if product is None:
            r.actions.append(f"rollup-skipped:{product_key}")
            return r
        findings = self.c12.rollup(product, trigger, trigger_id)
        r.actions.append(f"c12.rollup:{product_key}")
        r.findings += len(findings)
        return r


def handle_feed_event(deps: Deps, envelope: dict[str, Any]) -> HandlerResult:
    """`kc-evaluator` push: a table's aspects changed, or a product's entry changed."""
    payload, message_id = decode_push(envelope)
    entry = extract_entry_name(payload)
    r = HandlerResult()
    if not entry:
        r.actions.append("ignored:no-entry")
        return r
    ev = Evaluator(deps)
    trigger_id = message_id or entry
    ref = TableRef.from_entry_name(entry)
    if ref is not None:
        res = ev.evaluate_table(ref, "EVENT", trigger_id)
        r.actions += res.actions
        r.findings += res.findings
        if ev.c12:
            for p in deps.registry.products_of_table(ref):
                task = deps.queue.enqueue(p.key, deps.clock())
                r.actions.append(f"rollup-enqueued:{p.key}:{'new' if task else 'debounced'}")
        return r
    for p in deps.registry.all():
        if p.data.get("dataplex_entry") == entry and ev.c12:
            task = deps.queue.enqueue(p.key, deps.clock())
            r.actions.append(f"rollup-enqueued:{p.key}:{'new' if task else 'debounced'}")
    if not r.actions:
        r.actions.append(f"ignored:{entry}")
    return r


def handle_manifest_upload(deps: Deps, upload: StateUpload) -> HandlerResult:
    s = deps.settings
    r = HandlerResult()
    parsed = parse_lineage_path(upload.path)
    if parsed is None or parsed[0] != s.env:
        r.actions.append("ignored:not-a-manifest")
        return r
    _, domain = parsed

    def reject(reason: str) -> HandlerResult:
        f = make_finding(
            env=s.env,
            control="C14",
            check="C14.manifest_source",
            asset=domain,
            asset_type="DATA_DOMAIN",
            status="FAIL",
            evaluator_version=MANIFEST_VERSION,
            trigger="EVENT",
            trigger_id=upload.event_id,
            source_ref=f"gs://{upload.bucket}/{upload.path}",
            now=deps.clock(),
            domain=domain,
            detail={"reason": reason, "uploader": upload.uploader},
        )
        deps.publisher.publish(f)
        r.actions.append(f"manifest-rejected:{reason}")
        r.findings += 1
        return r

    if not uploader_allowed(domain, upload.uploader, s.cicd_project_id, s.single_deployer):
        return reject(f"uploader {upload.uploader!r} is not the pipeline of {domain}")
    data = deps.store.read_json(upload.path)
    if data is None:
        r.actions.append("ignored:object-gone")
        return r
    errs = schemas.errors("lineage_manifest", data)
    if errs:
        return reject("schema: " + "; ".join(errs[:5]))
    if data["domain"] != domain:
        return reject("manifest domain does not match the object path")
    ev = Evaluator(deps)
    if ev.c14 is None:
        return r
    for target in sorted(data["targets"]):
        res = ev.evaluate_table(TableRef.parse(target), "EVENT", upload.event_id)
        r.actions += res.actions
        r.findings += res.findings
    return r


def handle_state_event(deps: Deps, body: dict[str, Any], headers: dict[str, str]) -> HandlerResult:
    """Eventarc audit event for an object created in the hub state bucket (design §2.5)."""
    upload = parse_audit_event(body, headers)
    r = HandlerResult()
    if upload is None or upload.bucket != deps.settings.hub_state_bucket:
        r.actions.append("ignored:not-state-bucket")
        return r
    if upload.path.startswith("products/"):
        res = ContractWriter(deps).handle(upload)
        r.actions.append(f"contract:{'written' if res.written else res.reason}")
        r.findings += 1 if res.finding else 0
        return r
    if upload.path.startswith("lineage/"):
        return handle_manifest_upload(deps, upload)
    r.actions.append("ignored:path")
    return r


def handle_rollup_task(deps: Deps, body: dict[str, Any], task_name: str) -> HandlerResult:
    key = body.get("product", "")
    if key.count("/") != 2:
        return HandlerResult(actions=["ignored:bad-product-key"])
    return Evaluator(deps).rollup(key, "EVENT", task_name or key)
