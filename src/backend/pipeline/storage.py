"""In-memory stand-in for S3 and DynamoDB.

The Lambda handlers use boto3. Tests and the local script use MemoryStore
so they do not need AWS.
"""

from __future__ import annotations

from typing import Any


def job_key(job_id: str, comment_id: str) -> str:
    return f"jobs/{job_id}/comments/{comment_id}.json"


def results_key(job_id: str) -> str:
    return f"jobs/{job_id}/results.json"


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, Any] = {}
        self.items: list[dict[str, Any]] = []

    def exists(self, key: str) -> bool:
        return key in self.objects

    def put_json(self, key: str, payload: Any) -> None:
        self.objects[key] = payload

    def get_json(self, key: str) -> Any:
        return self.objects.get(key)

    def list_comments(self, job_id: str) -> list[dict[str, Any]]:
        prefix = f"jobs/{job_id}/comments/"
        return [value for key, value in sorted(self.objects.items()) if key.startswith(prefix)]

    def put_job(self, job: dict[str, Any]) -> None:
        pk = f"JOB#{job['jobId']}"
        self.items = [item for item in self.items if not (item.get("pk") == pk and item.get("sk") == "META")]
        self.items.append({"pk": pk, "sk": "META", **job})

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        pk = f"JOB#{job_id}"
        for item in self.items:
            if item.get("pk") == pk and item.get("sk") == "META":
                return dict(item)
        return None

    def put_finding(self, job_id: str, finding: dict[str, Any]) -> None:
        self.items.append({"pk": f"JOB#{job_id}", "sk": f"FINDING#{finding['findingId']}", **finding})

    def findings(self, job_id: str) -> list[dict[str, Any]]:
        pk = f"JOB#{job_id}"
        rows = [item for item in self.items if item.get("pk") == pk and str(item.get("sk", "")).startswith("FINDING#")]
        rows.sort(key=lambda item: str(item.get("sk")))
        return rows

    def put_latest(self, document_id: str, job_id: str) -> None:
        pk = f"DOC#{document_id}"
        self.items = [item for item in self.items if not (item.get("pk") == pk and item.get("sk") == "LATEST")]
        self.items.append({"pk": pk, "sk": "LATEST", "documentId": document_id, "jobId": job_id})

    def latest_job_id(self, document_id: str) -> str | None:
        pk = f"DOC#{document_id}"
        for item in self.items:
            if item.get("pk") == pk and item.get("sk") == "LATEST":
                return str(item["jobId"])
        return None
