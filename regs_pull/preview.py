"""One-call look at a document before anyone starts a long pull."""

from __future__ import annotations

from typing import Any

from regs_pull.engine import (
    PAGE_SIZE,
    RegulationsClient,
    as_int,
    doc_attrs,
    fetch_document,
    first_page_meta,
)

# Detail calls dominate. Listing is one page per 250 comments, and the
# document lookup is a single call. Attachment files are not API calls.
DEFAULT_RATE_PER_HOUR = 1000


def estimate_calls(total_comments: int) -> int:
    total = max(0, total_comments)
    list_pages = 1 if total == 0 else (total + PAGE_SIZE - 1) // PAGE_SIZE
    return 1 + list_pages + total


def preview_document(
    client: RegulationsClient,
    document_id: str,
    *,
    rate_per_hour: int = DEFAULT_RATE_PER_HOUR,
) -> dict[str, Any]:
    document = fetch_document(client, document_id)
    attributes = doc_attrs(document)
    object_id = attributes.get("objectId")
    total: Any = None
    if object_id:
        _items, meta = first_page_meta(
            client, "/comments", {"filter[commentOnId]": object_id}
        )
        total = meta.get("totalElements")
    total_count = as_int(total) or 0
    calls = estimate_calls(total_count)
    rate = rate_per_hour if rate_per_hour > 0 else DEFAULT_RATE_PER_HOUR
    document_type = str(attributes.get("documentType") or "")
    proposed = document_type.casefold() == "proposed rule"
    resolved_id = str(document.get("id") or document_id)
    return {
        "documentId": resolved_id,
        "title": attributes.get("title") or "",
        "agencyId": attributes.get("agencyId") or "",
        "documentType": document_type,
        "docketId": attributes.get("docketId") or "",
        "openForComment": bool(attributes.get("openForComment")),
        "commentStartDate": attributes.get("commentStartDate") or "",
        "commentEndDate": attributes.get("commentEndDate") or "",
        "postedDate": attributes.get("postedDate") or "",
        "objectId": object_id or "",
        "totalComments": total if total is not None else 0,
        "estimatedApiCalls": calls,
        "estimatedHours": round(calls / rate, 2),
        "ratePerHour": rate,
        "proposedRule": proposed,
        "warning": None if proposed else f"This document is a {document_type or 'unknown type'}, not a Proposed Rule.",
        "regulationsGovUrl": f"https://www.regulations.gov/document/{resolved_id}",
    }
