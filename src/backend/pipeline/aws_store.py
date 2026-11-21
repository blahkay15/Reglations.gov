"""S3 and DynamoDB adapter used by the two Lambdas."""

from __future__ import annotations

import json
from typing import Any

from storage import job_key, results_key


class DynamoS3Store:
    def __init__(self, bucket: str, table_name: str) -> None:
        import boto3

        self.bucket = bucket
        self._s3 = boto3.client("s3")
        self._table = boto3.resource("dynamodb").Table(table_name)

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def put_json(self, key: str, payload: Any) -> None:
        self._s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(payload).encode("utf-8"),
            ContentType="application/json",
        )

    def get_json(self, key: str) -> Any:
        try:
            response = self._s3.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return json.loads(response["Body"].read().decode("utf-8"))

    def list_comments(self, job_id: str) -> list[dict[str, Any]]:
        prefix = f"jobs/{job_id}/comments/"
        comments: list[dict[str, Any]] = []
        token = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self._s3.list_objects_v2(**kwargs)
            for item in page.get("Contents") or []:
                payload = self.get_json(item["Key"])
                if isinstance(payload, dict):
                    comments.append(payload)
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return comments

    def put_job(self, job: dict[str, Any]) -> None:
        item = {"pk": f"JOB#{job['jobId']}", "sk": "META", **_ddb(job)}
        self._table.put_item(Item=item)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        response = self._table.get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"})
        return response.get("Item")

    def put_finding(self, job_id: str, finding: dict[str, Any]) -> None:
        self._table.put_item(Item={"pk": f"JOB#{job_id}", "sk": f"FINDING#{finding['findingId']}", **_ddb(finding)})

    def findings(self, job_id: str) -> list[dict[str, Any]]:
        response = self._table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={":pk": f"JOB#{job_id}", ":sk": "FINDING#"},
        )
        return list(response.get("Items") or [])

    def put_latest(self, document_id: str, job_id: str) -> None:
        self._table.put_item(Item={"pk": f"DOC#{document_id}", "sk": "LATEST", "documentId": document_id, "jobId": job_id})

    def latest_job_id(self, document_id: str) -> str | None:
        response = self._table.get_item(Key={"pk": f"DOC#{document_id}", "sk": "LATEST"})
        item = response.get("Item")
        return str(item["jobId"]) if item else None

    def put_results(self, job_id: str, payload: dict[str, Any]) -> None:
        self.put_json(results_key(job_id), payload)


def _ddb(value: Any) -> Any:
    """DynamoDB rejects empty strings and floats. Keep the item small and plain."""
    if isinstance(value, dict):
        return {key: _ddb(item) for key, item in value.items() if item not in ("", None, [])}
    if isinstance(value, list):
        return [_ddb(item) for item in value]
    if isinstance(value, float):
        return str(value)
    return value


# Imported so the key helper stays the one storage.py owns.
_ = job_key
