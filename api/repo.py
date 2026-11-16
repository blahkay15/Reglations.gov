"""Job and file storage used by the API.

``MemoryRepo`` is what the tests drive. ``DiskRepo`` is the local dev
server. The Lambda process uses ``api.aws_repo.AwsRepo``, which is imported
only when that process starts, because boto3 is provided by the Lambda
runtime rather than by the CLI's requirements.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class MemoryRepo:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str], dict[str, Any]] = {}
        self.watches: dict[tuple[str, str], dict[str, Any]] = {}
        self.objects: dict[str, bytes] = {}
        self.started: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def create_job(self, user_id: str, job: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.jobs[(user_id, job["jobId"])] = dict(job)
            return dict(job)

    def list_jobs(self, user_id: str) -> list[dict[str, Any]]:
        rows = [dict(job) for (owner, _job_id), job in self.jobs.items() if owner == user_id]
        rows.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return rows

    def get_job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get((user_id, job_id))
        return dict(job) if job else None

    def update_job(self, user_id: str, job_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            current = self.jobs.get((user_id, job_id))
            if current is None:
                return None
            current.update(fields)
            current["updatedAt"] = _now()
            return dict(current)

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.objects[key] = data

    def get_object(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def presign(self, key: str) -> str:
        return f"/api/local-file?key={key}"

    def start_job(self, job: dict[str, Any]) -> None:
        self.started.append(dict(job))

    def put_watch(self, user_id: str, watch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.watches[(user_id, watch["documentId"])] = dict(watch)
            return self.public_watch(watch)

    def list_watches(self, user_id: str) -> list[dict[str, Any]]:
        rows = [self.public_watch(watch) for (owner, _doc), watch in self.watches.items() if owner == user_id]
        rows.sort(key=lambda item: item.get("documentId") or "")
        return rows

    def get_watch(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        watch = self.watches.get((user_id, document_id))
        return dict(watch) if watch else None

    def delete_watch(self, user_id: str, document_id: str) -> bool:
        return self.watches.pop((user_id, document_id), None) is not None

    def update_watch_seen(self, user_id: str, document_id: str, seen_ids: list[str]) -> None:
        watch = self.watches.get((user_id, document_id))
        if watch is None:
            return
        watch["seenIds"] = list(seen_ids)
        watch["lastCheckedAt"] = _now()

    def list_all_watches(self) -> list[dict[str, Any]]:
        return [dict(watch) for watch in self.watches.values()]

    @staticmethod
    def public_watch(watch: dict[str, Any]) -> dict[str, Any]:
        """Fields safe to send to the browser. The webhook URL is not one of them."""
        return {
            "documentId": watch.get("documentId"),
            "createdAt": watch.get("createdAt"),
            "lastCheckedAt": watch.get("lastCheckedAt"),
            "webhookConfigured": bool(watch.get("webhookUrl") or watch.get("webhookSecretArn")),
        }


class DiskRepo(MemoryRepo):
    """MemoryRepo plus a directory, so a local worker and the API share state."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    def _job_path(self, user_id: str, job_id: str) -> Path:
        return self.root / "jobs" / user_id / f"{job_id}.json"

    def _watch_path(self, user_id: str, document_id: str) -> Path:
        safe = document_id.replace("/", "_")
        return self.root / "watches" / user_id / f"{safe}.json"

    def _object_path(self, key: str) -> Path:
        return self.root / "objects" / key

    def _load(self) -> None:
        jobs_root = self.root / "jobs"
        if jobs_root.exists():
            for path in jobs_root.glob("*/*.json"):
                job = json.loads(path.read_text(encoding="utf-8"))
                self.jobs[(job["userId"], job["jobId"])] = job
        watches_root = self.root / "watches"
        if watches_root.exists():
            for path in watches_root.glob("*/*.json"):
                watch = json.loads(path.read_text(encoding="utf-8"))
                self.watches[(watch["userId"], watch["documentId"])] = watch

    def create_job(self, user_id: str, job: dict[str, Any]) -> dict[str, Any]:
        saved = super().create_job(user_id, job)
        path = self._job_path(user_id, job["jobId"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
        return saved

    def update_job(self, user_id: str, job_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        updated = super().update_job(user_id, job_id, fields)
        if updated is None:
            return None
        path = self._job_path(user_id, job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        return updated

    def put_watch(self, user_id: str, watch: dict[str, Any]) -> dict[str, Any]:
        public = super().put_watch(user_id, watch)
        path = self._watch_path(user_id, watch["documentId"])
        path.parent.mkdir(parents=True, exist_ok=True)
        stored = dict(self.watches[(user_id, watch["documentId"])])
        path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        return public

    def delete_watch(self, user_id: str, document_id: str) -> bool:
        removed = super().delete_watch(user_id, document_id)
        path = self._watch_path(user_id, document_id)
        if path.exists():
            path.unlink()
        return removed

    def update_watch_seen(self, user_id: str, document_id: str, seen_ids: list[str]) -> None:
        super().update_watch_seen(user_id, document_id, seen_ids)
        watch = self.watches.get((user_id, document_id))
        if watch is None:
            return
        path = self._watch_path(user_id, watch["documentId"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(watch, indent=2) + "\n", encoding="utf-8")

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        super().put_object(key, data)
        path = self._object_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_object(self, key: str) -> bytes | None:
        cached = super().get_object(key)
        if cached is not None:
            return cached
        path = self._object_path(key)
        if not path.is_file():
            return None
        data = path.read_bytes()
        self.objects[key] = data
        return data


Starter = Callable[[dict[str, Any]], None]
