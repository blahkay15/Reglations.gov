"""Route HTTP requests for the comment app.

The Lambda entrypoint and the local server both call :func:`route`. Tests
call it with a ``MemoryRepo`` and a fake preview function, so nothing here
talks to AWS or regulations.gov unless the caller asks it to.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, unquote

from api.repo import MemoryRepo

DOCUMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
ADMIN_GROUP = "regs-admins"
PreviewFn = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class Actor:
    """Who is calling. ``user_id`` is the Cognito sub."""

    user_id: str
    email: str = ""
    name: str = ""
    admin: bool = False

    def can_manage(self, owner_id: str) -> bool:
        return self.admin or self.user_id == owner_id


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_job(actor: Actor, document_id: str) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "jobId": uuid.uuid4().hex,
        "userId": actor.user_id,
        "ownerName": actor.name or actor.email or actor.user_id,
        "ownerEmail": actor.email,
        "scope": "team",
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


def present_job(job: dict[str, Any], actor: Actor) -> dict[str, Any]:
    shown = dict(job)
    shown["canManage"] = actor.can_manage(str(job.get("userId") or ""))
    return shown


def present_watch(watch: dict[str, Any], actor: Actor) -> dict[str, Any]:
    shown = MemoryRepo.public_watch(watch)
    shown["canManage"] = actor.can_manage(str(watch.get("userId") or ""))
    return shown


def route(
    method: str,
    path: str,
    query: dict[str, str],
    body: dict[str, Any] | None,
    user_id: str,
    repo: MemoryRepo,
    *,
    actor: Actor | None = None,
    previewer: PreviewFn | None = None,
) -> tuple[int, dict[str, Any]]:
    method = method.upper()
    path = path.rstrip("/") or "/"
    body = body or {}
    who = actor or Actor(user_id=user_id, name=user_id)

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
        job = new_job(who, document_id)
        if body.get("title"):
            job["title"] = str(body["title"])
        repo.create_job(who.user_id, job)
        repo.start_job(job)
        return 202, present_job(job, who)

    if method == "GET" and path == "/api/jobs":
        scope = (query.get("scope") or "mine").lower()
        if scope == "team":
            rows = repo.list_team_jobs()
        elif scope == "mine":
            rows = repo.list_jobs(who.user_id)
        else:
            return 400, {"error": "scope must be mine or team."}
        return 200, {"jobs": [present_job(job, who) for job in rows]}

    job_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)", path)
    if job_match and method == "GET":
        job = repo.find_job(job_match.group(1))
        if job is None:
            return 404, {"error": "Job not found."}
        return 200, present_job(job, who)

    if job_match and method == "DELETE":
        job = repo.find_job(job_match.group(1))
        if job is None:
            return 404, {"error": "Job not found."}
        if not who.can_manage(str(job.get("userId") or "")):
            return 403, {"error": "Only the person who started this pull, or an admin, can cancel it."}
        owner = str(job["userId"])
        job_id = job_match.group(1)
        if job.get("status") in {"queued", "running"}:
            updated = repo.update_job(owner, job_id, {"status": "cancelled"})
            return 200, present_job(updated or job, who)
        repo.delete_job(owner, job_id)
        return 200, {"deleted": job_id}

    comments_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)/comments", path)
    if comments_match and method == "GET":
        job_id = comments_match.group(1)
        if repo.find_job(job_id) is None:
            return 404, {"error": "Job not found."}
        rows = _json_object(repo, f"jobs/{job_id}/comments_index.json") or []
        return 200, _page(_filter_comments(rows, query), query)

    summary_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)/summary", path)
    if summary_match and method == "GET":
        job_id = summary_match.group(1)
        if repo.find_job(job_id) is None:
            return 404, {"error": "Job not found."}
        summary = _json_object(repo, f"jobs/{job_id}/summary.json")
        if summary is None:
            return 404, {"error": "Summary is not ready yet."}
        return 200, summary

    download_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9]+)/download", path)
    if download_match and method == "GET":
        job_id = download_match.group(1)
        if repo.find_job(job_id) is None:
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
        scope = (query.get("scope") or "mine").lower()
        if scope == "team":
            rows = repo.list_team_watches()
        elif scope == "mine":
            rows = repo.list_watches(who.user_id)
        else:
            return 400, {"error": "scope must be mine or team."}
        return 200, {"watches": [present_watch(watch, who) for watch in rows]}

    if method == "POST" and path == "/api/watches":
        document_id = str(body.get("documentId") or "").strip()
        webhook_url = str(body.get("webhookUrl") or "").strip()
        if not DOCUMENT_ID.match(document_id):
            return 400, {"error": "Enter a document ID to watch."}
        if not webhook_url.startswith("https://"):
            return 400, {"error": "The Teams webhook URL must be an https URL."}
        watch = {
            "userId": who.user_id,
            "ownerName": who.name or who.email or who.user_id,
            "ownerEmail": who.email,
            "scope": "team",
            "documentId": document_id,
            "webhookUrl": webhook_url,
            "createdAt": now_iso(),
            "lastCheckedAt": body.get("lastCheckedAt") or "",
            "seenIds": list(body.get("seenIds") or []),
        }
        repo.put_watch(who.user_id, watch)
        return 201, present_watch(watch, who)

    watch_owned = re.fullmatch(r"/api/watches/([^/]+)/([^/]+)", path)
    if watch_owned and method in {"DELETE", "PATCH"}:
        owner_id = unquote(watch_owned.group(1))
        document_id = unquote(watch_owned.group(2))
        return _change_watch(method, repo, who, owner_id, document_id, body)

    delete_match = re.fullmatch(r"/api/watches/([^/]+)", path)
    if delete_match and method == "DELETE":
        document_id = unquote(delete_match.group(1))
        return _change_watch("DELETE", repo, who, who.user_id, document_id, body)

    return 404, {"error": "Not found."}


def _change_watch(
    method: str,
    repo: MemoryRepo,
    actor: Actor,
    owner_id: str,
    document_id: str,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    watch = repo.get_watch(owner_id, document_id)
    if watch is None:
        return 404, {"error": "Watch not found."}
    if not actor.can_manage(owner_id):
        return 403, {"error": "Only the person who created this watch, or an admin, can change it."}
    if method == "DELETE":
        repo.delete_watch(owner_id, document_id)
        return 200, {"deleted": document_id, "userId": owner_id}
    webhook_url = str(body.get("webhookUrl") or "").strip()
    if not webhook_url.startswith("https://"):
        return 400, {"error": "The Teams webhook URL must be an https URL."}
    watch["webhookUrl"] = webhook_url
    repo.put_watch(owner_id, watch)
    return 200, present_watch(watch, actor)


def _claim_groups(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(item) for item in value}
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return {str(item) for item in parsed}
    return {part for part in text.replace(",", " ").split() if part}


def actor_from_claims(claims: dict[str, Any], *, admin_group_id: str = "") -> Actor:
    groups = _claim_groups(claims.get("cognito:groups"))
    entra_groups = _claim_groups(claims.get("custom:groups"))
    admin = ADMIN_GROUP in groups or bool(admin_group_id and admin_group_id in entra_groups)
    name = str(claims.get("name") or claims.get("given_name") or claims.get("email") or "")
    return Actor(
        user_id=str(claims.get("sub") or claims.get("username") or ""),
        email=str(claims.get("email") or ""),
        name=name,
        admin=admin,
    )


def _query(event: dict[str, Any]) -> dict[str, str]:
    raw = event.get("queryStringParameters") or {}
    if raw:
        return {str(key): "" if value is None else str(value) for key, value in raw.items()}
    parsed = parse_qs(event.get("rawQueryString") or "", keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def actor_from_event(event: dict[str, Any]) -> Actor:
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    jwt = authorizer.get("jwt") or authorizer
    claims = jwt.get("claims") or {}
    if claims.get("sub") or claims.get("username"):
        return actor_from_claims(claims, admin_group_id=os.environ.get("ENTRA_ADMIN_GROUP_ID", ""))
    headers = {str(key).lower(): value for key, value in (event.get("headers") or {}).items()}
    if headers.get("x-local-user"):
        return Actor(
            user_id=str(headers["x-local-user"]),
            email=str(headers.get("x-local-email") or ""),
            name=str(headers.get("x-local-name") or headers["x-local-user"]),
            admin=str(headers.get("x-local-admin") or "").lower() in {"1", "true", "yes"},
        )
    return Actor(user_id="")


def lambda_handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """API Gateway HTTP API payload, format 2.0."""
    actor = actor_from_event(event)
    if not actor.user_id:
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
        actor.user_id,
        AwsRepo(),
        actor=actor,
        previewer=live_preview,
    )
    return _response(status, result)


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
