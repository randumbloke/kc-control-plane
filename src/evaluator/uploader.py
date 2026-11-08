"""Uploader check for state bucket objects (design §2.3.2).

Managed-folder IAM is the first control. As a second check, the evaluator confirms that the
identity that uploaded an object under `products/` or `lineage/` is the pipeline of the
domain named in the path, or `tf-deployer` when `single_deployer` is true (§6.7.2). If not,
the object is ignored and a finding is published.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cdmc_common.names import domain_pipeline_identities


@dataclass(frozen=True)
class StateUpload:
    bucket: str
    path: str
    uploader: str
    method: str
    event_id: str


def parse_audit_event(body: dict[str, Any], headers: dict[str, str]) -> StateUpload | None:
    """Parse the Eventarc Cloud Audit Logs event for `storage.objects.create`.

    VERIFY IN BUILD (VA-09): event delivery is binary-mode CloudEvents with the LogEntry as
    the body (`protoPayload.authenticationInfo.principalEmail`, `protoPayload.resourceName`
    = `projects/_/buckets/<bucket>/objects/<path>`). Structured mode (`data`) is accepted too.
    """
    entry = body.get("data", body) if isinstance(body, dict) else {}
    proto = entry.get("protoPayload") or {}
    resource = proto.get("resourceName") or ""
    if "/buckets/" not in resource or "/objects/" not in resource:
        subject = headers.get("ce-subject", "")
        resource = subject.split("storage.googleapis.com/", 1)[-1]
    if "/buckets/" not in resource or "/objects/" not in resource:
        return None
    bucket = resource.split("/buckets/", 1)[1].split("/objects/", 1)[0]
    path = resource.split("/objects/", 1)[1]
    uploader = (proto.get("authenticationInfo") or {}).get("principalEmail", "")
    event_id = headers.get("ce-id") or entry.get("insertId") or f"{bucket}/{path}"
    return StateUpload(bucket, path, uploader, proto.get("methodName", ""), event_id)


def uploader_allowed(
    domain: str, uploader: str, cicd_project_id: str, single_deployer: bool
) -> bool:
    try:
        allowed = domain_pipeline_identities(domain, cicd_project_id, single_deployer)
    except ValueError:
        return False
    return uploader.lower() in allowed
