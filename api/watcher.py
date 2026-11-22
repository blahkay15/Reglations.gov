"""Weekday check for watched documents.

The schedule is EventBridge Scheduler. This function asks regulations.gov
for the comment list, keeps ids it has not already reported, classifies
that new slice, and posts to Teams only when the slice is not empty.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from regs_pull.cluster import ClusterConfig
from regs_pull.engine import RegulationsClient, first_page_meta
from regs_pull.watch import comments_after, post_if_new, summarize_new_comments


def check_watch(
    watch: dict[str, Any],
    listed_comments: list[dict[str, Any]],
    *,
    poster: Callable[[str, dict[str, Any]], None],
    app_url: str = "",
    config: ClusterConfig | None = None,
) -> dict[str, Any]:
    """Pure check used by tests and by the Lambda.

    ``listed_comments`` are the newest comments the list endpoint returned.
    Each item needs ``id`` and, when text was fetched, ``text``.
    """
    seen = {str(item) for item in watch.get("seenIds") or []}
    fresh = comments_after(listed_comments, seen)
    summary = summarize_new_comments(str(watch.get("documentId") or ""), fresh, config=config, app_url=app_url)
    posted = post_if_new(str(watch.get("webhookUrl") or ""), summary, poster)
    summary["posted"] = posted
    summary["seenIds"] = sorted(seen | {str(comment.get("id") or "") for comment in fresh})
    return summary


def _list_newest(client: RegulationsClient, object_id: str) -> list[dict[str, Any]]:
    items, _meta = first_page_meta(client, "/comments", {"filter[commentOnId]": object_id})
    comments = []
    for item in items:
        attributes = item.get("attributes") or {}
        comments.append(
            {
                "id": item.get("id"),
                "text": attributes.get("title") or "",
                "postedDate": attributes.get("postedDate") or attributes.get("lastModifiedDate") or "",
            }
        )
    return comments


def lambda_handler(event: dict[str, Any] | None = None, _context: Any = None) -> dict[str, Any]:
    import boto3

    from api.aws_repo import AwsRepo
    from api.preview_live import api_key_from_secret
    from regs_pull.engine import DEFAULT_BASE_URL, fetch_document

    repo = AwsRepo()
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["API_KEY_SECRET_ARN"])
    key = api_key_from_secret(secret.get("SecretString") or "")
    client = RegulationsClient(os.environ.get("REGS_BASE_URL", DEFAULT_BASE_URL), key)
    app_url = os.environ.get("APP_URL", "")

    def poster(url: str, payload: dict[str, Any]) -> None:
        import requests

        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()

    results = []
    for watch in repo.list_all_watches():
        document = fetch_document(client, watch["documentId"])
        object_id = (document.get("attributes") or {}).get("objectId")
        listed = _list_newest(client, object_id) if object_id else []
        # The list endpoint has titles, not comment bodies. Classify on titles
        # so a wave of identical subject lines still shows up as a form letter.
        summary = check_watch(watch, listed, poster=poster, app_url=app_url)
        repo.update_watch_seen(watch["userId"], watch["documentId"], summary["seenIds"])
        results.append(
            {"documentId": watch["documentId"], "newCount": summary["newCount"], "posted": summary["posted"]}
        )
    return {"checked": len(results), "results": results}
