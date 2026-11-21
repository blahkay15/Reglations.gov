"""Fetch one page of regulations.gov comments into S3.

Step Functions calls this Lambda in three modes:

- plan: look up the document objectId and how many pages exist
- page: write that page's comment details
- next_window: decide whether another 20-page window is required

Attachments stay off unless the event sets downloadAttachments true.
"""

from __future__ import annotations

import math
import os
from typing import Any

from regs_pull.engine import PAGE_SIZE, RegulationsClient, eastern_filter_value, fetch_document

from storage import MemoryStore, job_key

MAX_PAGE = 20


class RateLimited(Exception):
    """The regulations.gov key asked us to wait. Step Functions retries this."""


def _guard(call):
    try:
        return call()
    except RateLimited:
        raise
    except Exception as exc:  # noqa: BLE001
        if "429" in str(exc):
            raise RateLimited(str(exc)) from exc
        raise


def comment_record(detail: dict[str, Any]) -> dict[str, Any]:
    data = detail.get("data") or {}
    attributes = data.get("attributes") or {}
    return {
        "id": str(data.get("id") or ""),
        "documentId": str(attributes.get("commentOnDocumentId") or attributes.get("documentId") or ""),
        "title": attributes.get("title") or "",
        "postedDate": attributes.get("postedDate") or "",
        "organization": attributes.get("organization") or "",
        "comment": attributes.get("comment") or "",
        "firstName": attributes.get("firstName") or "",
        "lastName": attributes.get("lastName") or "",
        "lastModifiedDate": attributes.get("lastModifiedDate") or "",
    }


def _page_count(total: int, page_size: int) -> int:
    if total <= 0:
        return 0
    return min(MAX_PAGE, max(1, math.ceil(total / page_size)))


def plan(event: dict[str, Any], client: RegulationsClient, store: MemoryStore) -> dict[str, Any]:
    document_id = str(event["documentId"])
    job_id = str(event["jobId"])
    page_size = int(event.get("pageSize") or os.environ.get("PAGE_SIZE") or 20)
    page_size = min(PAGE_SIZE, max(5, page_size))
    document = _guard(lambda: fetch_document(client, document_id))
    attributes = document.get("attributes") or {}
    object_id = attributes.get("objectId")
    if not object_id:
        raise RuntimeError(f"Document {document_id} has no objectId.")
    payload = _guard(
        lambda: client.get_json(
            "/comments",
            {
                "filter[commentOnId]": object_id,
                "page[size]": page_size,
                "page[number]": 1,
                "sort": "lastModifiedDate,documentId",
            },
        )
    )
    meta = payload.get("meta") or {}
    total = int(meta.get("totalElements") or 0)
    pages = list(range(1, _page_count(total, page_size) + 1))
    state = {
        "jobId": job_id,
        "documentId": document_id,
        "objectId": object_id,
        "title": attributes.get("title") or "",
        "pageSize": page_size,
        "totalComments": total,
        "pages": pages,
        "windowGe": None,
        "done": total == 0,
        "loops": 0,
    }
    store.put_job({**state, "status": "RUNNING" if not state["done"] else "EMPTY"})
    store.put_latest(document_id, job_id)
    return state


def pull_one_page(event: dict[str, Any], client: RegulationsClient, store: MemoryStore) -> dict[str, Any]:
    job_id = str(event["jobId"])
    object_id = str(event["objectId"])
    page = int(event["page"])
    page_size = int(event.get("pageSize") or 20)
    params: dict[str, Any] = {
        "filter[commentOnId]": object_id,
        "page[size]": page_size,
        "page[number]": page,
        "sort": "lastModifiedDate,documentId",
    }
    if event.get("windowGe"):
        params["filter[lastModifiedDate][ge]"] = event["windowGe"]
    payload = _guard(lambda: client.get_json("/comments", params))
    written = 0
    skipped = 0
    last_modified = ""
    for item in payload.get("data") or []:
        comment_id = str(item.get("id") or "")
        if not comment_id:
            continue
        key = job_key(job_id, comment_id)
        if store.exists(key):
            skipped += 1
            continue
        detail = _guard(lambda cid=comment_id: client.get_json(f"/comments/{cid}"))
        record = comment_record(detail if isinstance(detail, dict) else {})
        if not record["id"]:
            record["id"] = comment_id
        if event.get("downloadAttachments"):
            record["attachmentsRequested"] = True
        store.put_json(key, record)
        written += 1
        last_modified = record.get("lastModifiedDate") or last_modified
    return {"page": page, "written": written, "skipped": skipped, "lastModifiedDate": last_modified}


def next_window(event: dict[str, Any], client: RegulationsClient, store: MemoryStore) -> dict[str, Any]:
    """client is unused; the next window is decided from comments already in S3."""
    del client
    job_id = str(event["jobId"])
    total = int(event.get("totalComments") or 0)
    comments = store.list_comments(job_id)
    loops = int(event.get("loops") or 0) + 1
    state = {
        "jobId": job_id,
        "documentId": event.get("documentId"),
        "objectId": event.get("objectId"),
        "pageSize": int(event.get("pageSize") or 20),
        "totalComments": total,
        "loops": loops,
    }
    if len(comments) >= total or loops > 40:
        state.update({"pages": [], "windowGe": event.get("windowGe"), "done": True})
        store.put_job({**state, "status": "PULLED", "savedComments": len(comments)})
        return state
    dates = [str(comment.get("lastModifiedDate") or "") for comment in comments if comment.get("lastModifiedDate")]
    window_ge = eastern_filter_value(max(dates)) if dates else event.get("windowGe")
    page_size = state["pageSize"]
    remaining = max(0, total - len(comments))
    pages = list(range(1, _page_count(remaining, page_size) + 1)) or [1]
    state.update({"pages": pages, "windowGe": window_ge, "done": False})
    return state


def handle(event: dict[str, Any], client: RegulationsClient, store: MemoryStore) -> dict[str, Any]:
    mode = event.get("mode") or "page"
    if mode == "plan":
        return plan(event, client, store)
    if mode == "next_window":
        return next_window(event, client, store)
    if mode == "page":
        return pull_one_page(event, client, store)
    raise ValueError(f"Unknown mode {mode}")


def _client_from_env() -> RegulationsClient:
    import boto3

    from regs_pull.engine import DEFAULT_BASE_URL

    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["API_KEY_SECRET_ARN"])
    raw = secret.get("SecretString") or ""
    return RegulationsClient(os.environ.get("REGS_BASE_URL", DEFAULT_BASE_URL), raw.strip())


def _store_from_env():
    from aws_store import DynamoS3Store

    return DynamoS3Store(os.environ["BUCKET_NAME"], os.environ["TABLE_NAME"])


def handler(event, _context=None):
    return handle(event or {}, _client_from_env(), _store_from_env())
