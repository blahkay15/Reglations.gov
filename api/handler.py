"""Route HTTP requests for the comment app.

The Lambda entrypoint and the local server both call :func:`route`. Tests
call it with a ``MemoryRepo`` and a fake preview function, so nothing here
talks to AWS or regulations.gov unless the caller asks it to.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, unquote

from api.repo import MemoryRepo

DOCUMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
PreviewFn = Callable[[str], dict[str, Any]]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_job(user_id: str, document_id: str) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "jobId": uuid.uuid4().hex,
        "userId": user_id,
        "documentId": document_id,
        "status": "queued",
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "queued": 0,
        "fetched": 0,
        "attachmentsDownloaded": 0,
        "attachmentFailures": 0,
        "totalComments": None,
        "title": "",
        "error": "",
    }


def _json_object(repo: MemoryRepo, key: str) -> Any:
    raw = repo.get_object(key)
    if raw is None:
        return None
    return json.loads(raw.decode("utf-8"))


def _filter_comments(rows: list[dict[str, Any]], query: dict[str, str]) -> list[dict[str, Any]]:
    text = (query.get("q") or "").strip().casefold()
    organization = (query.get("organization") or "").strip().casefold()
    cluster = (query.get("cluster") or "").strip()
    form = (query.get("form") or "").strip().casefold()
    selected: list[dict[str, Any]] = []
    for row in rows:
        if text:
            haystack = " ".join(
                str(row.get(field) or "") for field in ("title", "comment", "organization", "id")
            ).casefold()
            if text not in haystack:
                continue
        if organization and organization not in str(row.get("organization") or "").casefold():
            continue
        if cluster and str(row.get("clusterId") or "") != cluster:
            continue
        if form in {"yes", "no"} and str(row.get("isFormLetter") or "no") != form:
            continue
        selected.append(row)
    return selected


def _page(rows: list[dict[str, Any]], query: dict[str, str]) -> dict[str, Any]:
    try:
        page = max(1, int(query.get("page") or "1"))
    except ValueError:
        page = 1
    try:
        page_size = min(100, max(1, int(query.get("pageSize") or "25")))
    except ValueError:
        page_size = 25
    start = (page - 1) * page_size
    return {
        "page": page,
        "pageSize": page_size,
        "total": len(rows),
        "comments": rows[start : start + page_size],
    }


def route(
    method: str,
    path: str,
    query: dict[str, str],
    body: dict[str, Any] | None,
    user_id: str,
    repo: MemoryRepo,
    *,
    previewer: PreviewFn | None = None,
) -> tuple[int, dict[str, Any]]:
    method = method.upper()
    path = path.rstrip("/") or "/"
    body = body or {}

    if method == "GET" and path == "/api/preview":
        document_id = (query.get("documentId") or "").strip()
        if not DOCUMENT_ID.match(document_id):
            return 400, {"error": "Enter a document ID such as FDA-2025-N-3262-0001."}
        if previewer is None:
            return 500, {"error": "Preview is not configured."}
        try:
            return 200, previewer(document_id)
        except Exception as exc:  # noqa: BLE001 — surface a useful message, not a stack
            return 502, {"error": str(exc)}

    if method == "POST" and path == "/api/jobs":
        document_id = str(body.get("documentId") or "").strip()
        if not DOCUMENT_ID.match(document_id):
            return 400, {"error": "Enter a document ID such as FDA-2025-N-3262-0001."}
        job = new_job(user_id, document_id)
        if body.get("title"):
            job["title"] = str(body["title"])
        repo.create_job(user_id, job)
        repo.start_job(job)
        return 202, job

    if method == "GET" and path == "/api/jobs":
        return 200, {"jobs": repo.list_jobs(user_id)}

    job_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)", path)
    if job_match and method == "GET":
        job = repo.get_job(user_id, job_match.group(1))
        if job is None:
            return 404, {"error": "Job not found."}
        return 200, job

    comments_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)/comments", path)
    if comments_match and method == "GET":
        job_id = comments_match.group(1)
        if repo.get_job(user_id, job_id) is None:
            return 404, {"error": "Job not found."}
        rows = _json_object(repo, f"jobs/{job_id}/comments_index.json") or []
        return 200, _page(_filter_comments(rows, query), query)

    summary_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)/summary", path)
    if summary_match and method == "GET":
        job_id = summary_match.group(1)
        if repo.get_job(user_id, job_id) is None:
            return 404, {"error": "Job not found."}
        summary = _json_object(repo, f"jobs/{job_id}/summary.json")
        if summary is None:
            return 404, {"error": "Summary is not ready yet."}
        return 200, summary

    download_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)/download", path)
    if download_match and method == "GET":
        job_id = download_match.group(1)
        if repo.get_job(user_id, job_id) is None:
            return 404, {"error": "Job not found."}
        kind = query.get("kind") or "zip"
        names = {"zip": "comments.zip", "comments": "comments.csv", "attachments": "attachments.csv"}
        if kind not in names:
            return 400, {"error": "kind must be zip, comments, or attachments."}
        key = f"jobs/{job_id}/{names[kind]}"
        if repo.get_object(key) is None:
            return 404, {"error": "That file is not ready yet."}
        return 200, {"url": repo.presign(key), "key": key}

    if method == "GET" and path == "/api/watches":
        return 200, {"watches": repo.list_watches(user_id)}

    if method == "POST" and path == "/api/watches":
        document_id = str(body.get("documentId") or "").strip()
        webhook_url = str(body.get("webhookUrl") or "").strip()
        if not DOCUMENT_ID.match(document_id):
            return 400, {"error": "Enter a document ID to watch."}
        if not webhook_url.startswith("https://"):
            return 400, {"error": "The Teams webhook URL must be an https URL."}
        watch = {
            "userId": user_id,
            "documentId": document_id,
            "webhookUrl": webhook_url,
            "createdAt": now_iso(),
            "lastCheckedAt": body.get("lastCheckedAt") or "",
            "seenIds": list(body.get("seenIds") or []),
        }
        return 201, repo.put_watch(user_id, watch)

    delete_match = re.fullmatch(r"/api/watches/([^/]+)", path)
    if delete_match and method == "DELETE":
        document_id = unquote(delete_match.group(1))
        if not repo.delete_watch(user_id, document_id):
            return 404, {"error": "Watch not found."}
        return 200, {"deleted": document_id}

    return 404, {"error": "Not found."}


def _query(event: dict[str, Any]) -> dict[str, str]:
    raw = event.get("queryStringParameters") or {}
    if raw:
        return {str(key): "" if value is None else str(value) for key, value in raw.items()}
    parsed = parse_qs(event.get("rawQueryString") or "", keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def _user_id(event: dict[str, Any]) -> str:
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    jwt = authorizer.get("jwt") or authorizer
    claims = jwt.get("claims") or {}
    user = claims.get("sub") or claims.get("username")
    if user:
        return str(user)
    headers = {str(key).lower(): value for key, value in (event.get("headers") or {}).items()}
    if headers.get("x-local-user"):
        return str(headers["x-local-user"])
    return ""


def lambda_handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """API Gateway HTTP API payload, format 2.0."""
    user_id = _user_id(event)
    if not user_id:
        return _response(401, {"error": "Sign in required."})
    from api.aws_repo import AwsRepo
    from api.preview_live import live_preview

    try:
        payload = json.loads(event.get("body") or "{}") if event.get("body") else {}
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be JSON."})
    status, result = route(
        event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "GET",
        event.get("rawPath") or event.get("path") or "/",
        _query(event),
        payload if isinstance(payload, dict) else {},
        user_id,
        AwsRepo(),
        previewer=live_preview,
    )
    return _response(status, result)


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
