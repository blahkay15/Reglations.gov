"""Decide whether a watched document has anything new worth posting.

The scheduled checker supplies the comments it has not reported before.
This module only diffs and classifies them. When the list is empty the
caller must not contact Teams.
"""

from __future__ import annotations

from typing import Any, Callable

from regs_pull.cluster import ClusterConfig, cluster_comments

Poster = Callable[[str, dict[str, Any]], None]


def comments_after(comments: list[dict[str, Any]], seen_ids: set[str]) -> list[dict[str, Any]]:
    return [comment for comment in comments if str(comment.get("id") or "") not in seen_ids]


def summarize_new_comments(
    document_id: str,
    comments: list[dict[str, Any]],
    *,
    config: ClusterConfig | None = None,
    app_url: str = "",
) -> dict[str, Any]:
    """``comments`` are the ones newer than the last check. Each needs id and text."""
    if not comments:
        return {
            "documentId": document_id,
            "newCount": 0,
            "formLetterCount": 0,
            "uniqueCount": 0,
            "silent": True,
            "documentUrl": f"https://www.regulations.gov/document/{document_id}",
            "appUrl": app_url,
        }
    assignments = cluster_comments(
        [{"id": str(comment.get("id") or ""), "text": str(comment.get("text") or "")} for comment in comments],
        config,
    )
    form_letters = sum(1 for item in assignments if item["isFormLetter"])
    return {
        "documentId": document_id,
        "newCount": len(comments),
        "formLetterCount": form_letters,
        "uniqueCount": len(comments) - form_letters,
        "silent": False,
        "documentUrl": f"https://www.regulations.gov/document/{document_id}",
        "appUrl": app_url,
    }


def teams_message(summary: dict[str, Any]) -> dict[str, Any]:
    """Adaptive Card payload for a Teams Workflows webhook.

    Matches the body expected by Power Automate's "Post to a channel when a
    webhook request is received" trigger when that flow posts an Adaptive Card.
    """
    title = f"{summary['newCount']} new comment" + ("" if summary["newCount"] == 1 else "s")
    title += f" on {summary['documentId']}"
    detail = (
        f"{summary['formLetterCount']} look like form letters. "
        f"{summary['uniqueCount']} look unique."
    )
    actions: list[dict[str, str]] = [
        {"type": "Action.OpenUrl", "title": "Open on regulations.gov", "url": summary["documentUrl"]}
    ]
    if summary.get("appUrl"):
        actions.append({"type": "Action.OpenUrl", "title": "Open the app", "url": summary["appUrl"]})
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
                        {"type": "TextBlock", "text": detail, "wrap": True},
                    ],
                    "actions": actions,
                },
            }
        ],
    }


def post_if_new(webhook_url: str, summary: dict[str, Any], poster: Poster) -> bool:
    """Post only when there is something new. Returns whether a post was made."""
    if summary.get("silent") or not summary.get("newCount"):
        return False
    if not webhook_url:
        raise ValueError("webhook URL is missing")
    poster(webhook_url, teams_message(summary))
    return True
