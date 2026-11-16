"""DynamoDB, S3, and ECS backing for the API when it runs in AWS."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

from api.repo import MemoryRepo, _now


class AwsRepo(MemoryRepo):
    """Same methods as MemoryRepo, but each call hits AWS.

    The in-memory dicts on the base class are unused. Webhook URLs are stored
    in Secrets Manager; the DynamoDB item keeps only the secret ARN.
    """

    def __init__(self) -> None:
        super().__init__()
        import boto3

        self._dynamodb = boto3.resource("dynamodb")
        self._s3 = boto3.client("s3")
        self._secrets = boto3.client("secretsmanager")
        self._ecs = boto3.client("ecs")
        self.job_table = self._dynamodb.Table(os.environ["JOB_TABLE"])
        self.watch_table = self._dynamodb.Table(os.environ["WATCH_TABLE"])
        self.bucket = os.environ["DATA_BUCKET"]

    def create_job(self, user_id: str, job: dict[str, Any]) -> dict[str, Any]:
        self.job_table.put_item(Item=job)
        return job

    def list_jobs(self, user_id: str) -> list[dict[str, Any]]:
        response = self.job_table.query(
            KeyConditionExpression="userId = :user",
            ExpressionAttributeValues={":user": user_id},
            ScanIndexForward=False,
        )
        rows = response.get("Items") or []
        rows.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return rows

    def list_team_jobs(self) -> list[dict[str, Any]]:
        return self._query_scope(self.job_table)

    def get_job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        response = self.job_table.get_item(Key={"userId": user_id, "jobId": job_id})
        return response.get("Item")

    def find_job(self, job_id: str) -> dict[str, Any] | None:
        response = self.job_table.query(
            IndexName="byJobId",
            KeyConditionExpression="jobId = :id",
            ExpressionAttributeValues={":id": job_id},
            Limit=1,
        )
        items = response.get("Items") or []
        return items[0] if items else None

    def delete_job(self, user_id: str, job_id: str) -> bool:
        if self.get_job(user_id, job_id) is None:
            return False
        self.job_table.delete_item(Key={"userId": user_id, "jobId": job_id})
        return True

    def _query_scope(self, table) -> list[dict[str, Any]]:
        response = table.query(
            IndexName="byScope",
            KeyConditionExpression="#scope = :team",
            ExpressionAttributeNames={"#scope": "scope"},
            ExpressionAttributeValues={":team": "team"},
            ScanIndexForward=False,
        )
        return list(response.get("Items") or [])

    def update_job(self, user_id: str, job_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_job(user_id, job_id)
        if current is None:
            return None
        current.update(fields)
        current["updatedAt"] = _now()
        self.job_table.put_item(Item=current)
        return current

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_object(self, key: str) -> bytes | None:
        try:
            response = self._s3.get_object(Bucket=self.bucket, Key=key)
        except self._s3.exceptions.NoSuchKey:
            return None
        except Exception as exc:  # noqa: BLE001
            error = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if error in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return response["Body"].read()

    def presign(self, key: str) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=int(os.environ.get("PRESIGN_SECONDS", "900")),
        )

    def start_job(self, job: dict[str, Any]) -> None:
        self._ecs.run_task(
            cluster=os.environ["ECS_CLUSTER"],
            taskDefinition=os.environ["TASK_DEFINITION"],
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": os.environ["SUBNET_IDS"].split(","),
                    "securityGroups": [os.environ["TASK_SECURITY_GROUP"]],
                    "assignPublicIp": os.environ.get("ASSIGN_PUBLIC_IP", "ENABLED"),
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": "worker",
                        "environment": [
                            {"name": "JOB_ID", "value": job["jobId"]},
                            {"name": "USER_ID", "value": job["userId"]},
                            {"name": "DOCUMENT_ID", "value": job["documentId"]},
                        ],
                    }
                ]
            },
        )

    def put_watch(self, user_id: str, watch: dict[str, Any]) -> dict[str, Any]:
        existing = self.watch_table.get_item(Key={"userId": user_id, "documentId": watch["documentId"]}).get("Item")
        if existing and existing.get("webhookSecretArn"):
            self._secrets.put_secret_value(SecretId=existing["webhookSecretArn"], SecretString=watch["webhookUrl"])
            secret_arn = existing["webhookSecretArn"]
        else:
            secret = self._secrets.create_secret(
                Name=f"regs-comments/teams/{user_id}/{watch['documentId']}",
                SecretString=watch["webhookUrl"],
                Description="Microsoft Teams incoming webhook for a watched regulations.gov document",
            )
            secret_arn = secret["ARN"]
        stored = {
            "userId": user_id,
            "ownerName": watch.get("ownerName") or "",
            "ownerEmail": watch.get("ownerEmail") or "",
            "scope": "team",
            "documentId": watch["documentId"],
            "webhookSecretArn": secret_arn,
            "createdAt": watch.get("createdAt") or (existing or {}).get("createdAt"),
            "lastCheckedAt": watch.get("lastCheckedAt") or "",
            "seenIds": watch.get("seenIds") or [],
        }
        self.watch_table.put_item(Item=stored)
        return self.public_watch(stored)

    def list_watches(self, user_id: str) -> list[dict[str, Any]]:
        response = self.watch_table.query(
            KeyConditionExpression="userId = :user",
            ExpressionAttributeValues={":user": user_id},
            ScanIndexForward=False,
        )
        return list(response.get("Items") or [])

    def list_team_watches(self) -> list[dict[str, Any]]:
        return self._query_scope(self.watch_table)

    def get_watch(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        response = self.watch_table.get_item(Key={"userId": user_id, "documentId": document_id})
        item = response.get("Item")
        if item and item.get("webhookSecretArn") and "webhookUrl" not in item:
            secret = self._secrets.get_secret_value(SecretId=item["webhookSecretArn"])
            item = dict(item)
            item["webhookUrl"] = secret.get("SecretString") or ""
        return item

    def delete_watch(self, user_id: str, document_id: str) -> bool:
        current = self.watch_table.get_item(Key={"userId": user_id, "documentId": document_id}).get("Item")
        if not current:
            return False
        if current.get("webhookSecretArn"):
            self._secrets.delete_secret(SecretId=current["webhookSecretArn"], ForceDeleteWithoutRecovery=True)
        self.watch_table.delete_item(Key={"userId": user_id, "documentId": document_id})
        return True

    def update_watch_seen(self, user_id: str, document_id: str, seen_ids: list[str]) -> None:
        self.watch_table.update_item(
            Key={"userId": user_id, "documentId": document_id},
            UpdateExpression="SET seenIds = :ids, lastCheckedAt = :at",
            ExpressionAttributeValues={":ids": list(seen_ids), ":at": _now()},
        )

    def list_all_watches(self) -> list[dict[str, Any]]:
        items = self.list_team_watches()
        hydrated = []
        for item in items:
            if item.get("webhookSecretArn"):
                secret = self._secrets.get_secret_value(SecretId=item["webhookSecretArn"])
                item = dict(item)
                item["webhookUrl"] = secret.get("SecretString") or ""
            hydrated.append(item)
        return hydrated


def quote_key(key: str) -> str:
    return quote(key, safe="/")
