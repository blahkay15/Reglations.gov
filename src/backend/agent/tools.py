"""Plain boto3 tools. The Strands decorators live in main.py so tests do not import Strands."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any


def _clients():
    import boto3

    session = boto3.Session()
    return {
        "sfn": session.client("stepfunctions"),
        "table": session.resource("dynamodb").Table(os.environ["TABLE_NAME"]),
        "state_machine_arn": os.environ["STATE_MACHINE_ARN"],
    }


def start_analysis(document_id: str, clients: dict[str, Any] | None = None) -> dict[str, Any]:
    if not document_id or not str(document_id).strip():
        raise ValueError("document_id is required")
    clients = clients or _clients()
    job_id = uuid.uuid4().hex
    clients["table"].put_item(
        Item={
            "pk": f"JOB#{job_id}",
            "sk": "META",
            "jobId": job_id,
            "documentId": document_id.strip(),
            "status": "QUEUED",
        }
    )
    clients["table"].put_item(
        Item={
            "pk": f"DOC#{document_id.strip()}",
            "sk": "LATEST",
            "documentId": document_id.strip(),
            "jobId": job_id,
        }
    )
    clients["sfn"].start_execution(
        stateMachineArn=clients["state_machine_arn"],
        name=job_id,
        input=json.dumps({"jobId": job_id, "documentId": document_id.strip()}),
    )
    return {"jobId": job_id, "documentId": document_id.strip(), "status": "QUEUED"}


def job_status(job_id: str, clients: dict[str, Any] | None = None) -> dict[str, Any]:
    clients = clients or _clients()
    response = clients["table"].get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"})
    item = response.get("Item")
    if not item:
        return {"jobId": job_id, "status": "NOT_FOUND"}
    return {
        "jobId": job_id,
        "documentId": item.get("documentId", ""),
        "status": item.get("status", "UNKNOWN"),
        "commentCount": item.get("commentCount"),
        "formLetterComments": item.get("formLetterComments"),
        "uniqueComments": item.get("uniqueComments"),
    }


def get_findings(
    job_id: str = "",
    document_id: str = "",
    commenter_type: str = "",
    stance: str = "",
    theme: str = "",
    form_letters_only: bool = False,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clients = clients or _clients()
    resolved = job_id.strip()
    if not resolved and document_id.strip():
        latest = clients["table"].get_item(Key={"pk": f"DOC#{document_id.strip()}", "sk": "LATEST"}).get("Item")
        resolved = str((latest or {}).get("jobId") or "")
    if not resolved:
        return {"error": "Provide job_id or document_id."}
    response = clients["table"].query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
        ExpressionAttributeValues={":pk": f"JOB#{resolved}", ":sk": "FINDING#"},
    )
    rows = []
    for item in response.get("Items") or []:
        if form_letters_only and not item.get("isFormLetter"):
            continue
        if commenter_type and str(item.get("commenterType") or "").lower() != commenter_type.lower():
            continue
        if stance and str(item.get("stance") or "").lower() != stance.lower():
            continue
        if theme and theme.lower() not in str(item.get("theme") or "").lower():
            continue
        rows.append(
            {
                "findingId": item.get("findingId"),
                "theme": item.get("theme"),
                "stance": item.get("stance"),
                "commenterType": item.get("commenterType"),
                "summary": item.get("summary"),
                "clusterSize": item.get("clusterSize"),
                "isFormLetter": bool(item.get("isFormLetter")),
                "representativeIds": item.get("representativeIds") or [],
            }
        )
    status = job_status(resolved, clients)
    return {
        "jobId": resolved,
        "status": status.get("status"),
        "formLetterComments": status.get("formLetterComments"),
        "uniqueComments": status.get("uniqueComments"),
        "findings": rows,
    }
