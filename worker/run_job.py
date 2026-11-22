"""Run one pull and publish the app's files.

The ECS task and the local dev server both call :func:`execute_job`.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

from regs_pull.cluster import ClusterConfig
from regs_pull.engine import parse_args, run
from regs_pull.report import enrich_output

CONTENT_TYPES = {
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json",
    ".zip": "application/zip",
}


def cluster_config_from_env() -> ClusterConfig:
    return ClusterConfig(
        threshold=float(os.environ.get("CLUSTER_THRESHOLD", "0.8")),
        shingle_size=int(os.environ.get("CLUSTER_SHINGLE_SIZE", "5")),
        num_perm=int(os.environ.get("CLUSTER_NUM_PERM", "64")),
        bands=int(os.environ.get("CLUSTER_BANDS", "16")),
        min_cluster_size=int(os.environ.get("CLUSTER_MIN_SIZE", "3")),
    )


def zip_output(out_dir: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out_dir.rglob("*")):
            if not path.is_file() or path == destination:
                continue
            archive.write(path, path.relative_to(out_dir).as_posix())


def publish_tree(repo: Any, job_id: str, out_dir: Path) -> None:
    for path in out_dir.rglob("*"):
        if not path.is_file():
            continue
        key = f"jobs/{job_id}/{path.relative_to(out_dir).as_posix()}"
        content_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        repo.put_object(key, path.read_bytes(), content_type)


def execute_job(job: dict[str, Any], client: Any, repo: Any, out_dir: Path) -> dict[str, Any]:
    user_id = job["userId"]
    job_id = job["jobId"]
    out_dir.mkdir(parents=True, exist_ok=True)
    repo.update_job(user_id, job_id, {"status": "running"})

    def on_progress(event: dict[str, Any]) -> None:
        fields: dict[str, Any] = {}
        name = event.get("event")
        if name == "planned":
            fields["totalComments"] = event.get("totalComments")
        elif name == "listed":
            fields["queued"] = event.get("queued")
        elif name == "comment":
            fields["fetched"] = event.get("fetched")
            fields["queued"] = event.get("queued")
        elif name == "attachment":
            fields["attachmentsDownloaded"] = event.get("downloaded")
            if event.get("ok") is False:
                current = repo.get_job(user_id, job_id) or {}
                fields["attachmentFailures"] = int(current.get("attachmentFailures") or 0) + 1
        current = repo.get_job(user_id, job_id) or {}
        if current.get("status") == "cancelled":
            raise RuntimeError("cancelled")
        if fields:
            repo.update_job(user_id, job_id, fields)

    try:
        args = parse_args(
            ["--document-id", job["documentId"], "--out", str(out_dir), "--no-attachments"]
            if os.environ.get("REGS_SKIP_ATTACHMENTS") == "1"
            else ["--document-id", job["documentId"], "--out", str(out_dir)]
        )
        if os.environ.get("MAX_COMMENTS"):
            args.max_comments = int(os.environ["MAX_COMMENTS"])
        code = run(args, client, on_progress=on_progress)
        if code != 0:
            raise RuntimeError(f"pull exited {code}")
        summary = enrich_output(out_dir, cluster_config_from_env())
        archive = out_dir / "comments.zip"
        zip_output(out_dir, archive)
        publish_tree(repo, job_id, out_dir)
        if (repo.get_job(user_id, job_id) or {}).get("status") == "cancelled":
            return summary
        repo.update_job(
            user_id,
            job_id,
            {
                "status": "succeeded",
                "commentCount": summary["commentCount"],
                "formLetterCount": summary["formLetterCount"],
                "uniqueCount": summary["uniqueCount"],
                "error": "",
            },
        )
        return summary
    except Exception as exc:
        if (repo.get_job(user_id, job_id) or {}).get("status") != "cancelled":
            repo.update_job(user_id, job_id, {"status": "failed", "error": str(exc)})
        raise
