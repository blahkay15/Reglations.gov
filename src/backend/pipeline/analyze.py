"""Cluster stored comments and ask Bedrock to label the distinct arguments."""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from regs_pull.cluster import ClusterConfig, cluster_comments

from storage import MemoryStore, results_key

Labeler = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]

DEFAULT_MODEL = "us.amazon.nova-lite-v1:0"


def groups_from_comments(comments: list[dict[str, Any]], config: ClusterConfig | None = None) -> list[dict[str, Any]]:
    settings = config or ClusterConfig(threshold=0.8, min_cluster_size=3)
    assignments = cluster_comments(
        [{"id": str(comment.get("id") or ""), "text": str(comment.get("comment") or comment.get("text") or "")} for comment in comments],
        settings,
    )
    by_id = {str(comment.get("id") or ""): comment for comment in comments}
    grouped: dict[str, list[dict[str, Any]]] = {}
    uniques: list[dict[str, Any]] = []
    for assignment in assignments:
        comment = by_id.get(str(assignment["id"]), {})
        if assignment["isFormLetter"]:
            grouped.setdefault(str(assignment["clusterId"]), []).append(comment)
        else:
            uniques.append(comment)

    groups: list[dict[str, Any]] = []
    for cluster_id, members in grouped.items():
        representative = max(members, key=lambda item: len(str(item.get("comment") or "")))
        groups.append(
            {
                "findingId": f"form-{cluster_id}",
                "clusterId": cluster_id,
                "isFormLetter": True,
                "clusterSize": len(members),
                "representativeIds": [str(member.get("id") or "") for member in members[:5]],
                "text": str(representative.get("comment") or ""),
                "organization": representative.get("organization") or "",
            }
        )
    for comment in uniques:
        comment_id = str(comment.get("id") or "")
        groups.append(
            {
                "findingId": f"unique-{comment_id}",
                "clusterId": "",
                "isFormLetter": False,
                "clusterSize": 1,
                "representativeIds": [comment_id],
                "text": str(comment.get("comment") or ""),
                "organization": comment.get("organization") or "",
            }
        )
    groups.sort(key=lambda item: (-int(item["clusterSize"]), str(item["findingId"])))
    return groups


def label_groups(groups: list[dict[str, Any]], labeler: Labeler, limit: int, batch_size: int = 8) -> list[dict[str, Any]]:
    selected = groups[:limit]
    labeled: list[dict[str, Any]] = []
    for start in range(0, len(selected), batch_size):
        batch = selected[start : start + batch_size]
        try:
            raw = labeler(batch)
        except Exception as exc:  # noqa: BLE001 — keep the cluster even if the model call fails
            raw = [{"theme": "unlabeled", "stance": "unknown", "commenterType": "unknown", "summary": str(exc)} for _ in batch]
        for group, extra in zip(batch, raw):
            labeled.append({**group, **_clean_label(extra)})
    return labeled


def _clean_label(raw: dict[str, Any] | None) -> dict[str, str]:
    raw = raw or {}
    return {
        "theme": str(raw.get("theme") or "unlabeled")[:120],
        "stance": str(raw.get("stance") or "unknown")[:40],
        "commenterType": str(raw.get("commenterType") or "unknown")[:40],
        "summary": str(raw.get("summary") or "")[:500],
    }


def analyze(event: dict[str, Any], store: MemoryStore, labeler: Labeler) -> dict[str, Any]:
    job_id = str(event["jobId"])
    document_id = str(event.get("documentId") or "")
    limit = int(event.get("maxLabeledClusters") or os.environ.get("MAX_LABELED_CLUSTERS") or 40)
    comments = store.list_comments(job_id)
    groups = groups_from_comments(comments)
    findings = label_groups(groups, labeler, limit)
    form_letters = sum(int(item["clusterSize"]) for item in findings if item["isFormLetter"])
    unique = sum(1 for item in groups if not item["isFormLetter"])
    summary = {
        "jobId": job_id,
        "documentId": document_id,
        "status": "SUCCEEDED",
        "commentCount": len(comments),
        "formLetterComments": form_letters,
        "uniqueComments": unique,
        "labeledFindings": len(findings),
        "findings": [
            {key: value for key, value in finding.items() if key != "text"}
            for finding in findings
        ],
    }
    store.put_json(results_key(job_id), summary)
    store.put_job(
        {
            "jobId": job_id,
            "documentId": document_id,
            "status": "SUCCEEDED",
            "commentCount": len(comments),
            "formLetterComments": form_letters,
            "uniqueComments": unique,
        }
    )
    for finding in findings:
        store.put_finding(job_id, finding)
    store.put_latest(document_id, job_id)
    return summary


def bedrock_labeler(model_id: str | None = None) -> Labeler:
    import boto3

    client = boto3.client("bedrock-runtime")
    chosen = model_id or os.environ.get("BEDROCK_MODEL_ID") or DEFAULT_MODEL

    def label(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prompt = {
            "comments": [
                {
                    "id": item["findingId"],
                    "organization": item.get("organization") or "",
                    "copies": item["clusterSize"],
                    "formLetter": item["isFormLetter"],
                    "text": str(item.get("text") or "")[:1200],
                }
                for item in batch
            ]
        }
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "You label public comments for a compliance analyst. "
                                "Reply with JSON only, an array with one object per comment id, "
                                "fields theme, stance (support, oppose, neutral, or mixed), "
                                "commenterType (industry, individual, advocacy, government, or unknown), "
                                "and summary. Input:\n"
                                + json.dumps(prompt)
                            )
                        }
                    ],
                }
            ],
            "inferenceConfig": {"maxTokens": 2000, "temperature": 0},
        }
        response = client.invoke_model(modelId=chosen, body=json.dumps(body))
        payload = json.loads(response["body"].read())
        text = payload["output"]["message"]["content"][0]["text"]
        parsed = json.loads(text[text.find("[") : text.rfind("]") + 1])
        by_id = {str(item.get("id") or ""): item for item in parsed}
        return [by_id.get(item["findingId"], {}) for item in batch]

    return label


def handler(event, _context=None):
    from aws_store import DynamoS3Store

    store = DynamoS3Store(os.environ["BUCKET_NAME"], os.environ["TABLE_NAME"])
    return analyze(event or {}, store, bedrock_labeler())
