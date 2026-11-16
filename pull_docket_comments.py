#!/usr/bin/env python3
"""Pull public comments for a regulations.gov docket via official API v4.

Documented flow (https://open.gsa.gov/api/regulationsgov/):
  1. GET /v4/dockets/{docketId}
  2. GET /v4/documents?filter[docketId]={docketId}  (paginate)
  3. For each document, GET /v4/comments?filter[commentOnId]={objectId}
     with page[size]=250, pages 1-20; if more than 5000 comments, continue
     with sort=lastModifiedDate,documentId and filter[lastModifiedDate][ge]=...
  4. GET /v4/comments/{commentId}?include=attachments for text and files.

List endpoints do not include comment body; detail does (OpenAPI Comment vs
CommentDetail). commentOnId is the parent document's objectId, not documentId.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

DEFAULT_BASE_URL = "https://api.regulations.gov/v4"
DEFAULT_KEY = "DEMO_KEY"
PAGE_SIZE = 250
MAX_PAGE = 20
EASTERN = ZoneInfo("America/New_York")
# Official example converted 2020-08-10T15:58:52Z to "2020-08-10 11:58:52" Eastern.
LAST_MOD_FILTER_FMT = "%Y-%m-%d %H:%M:%S"

CSV_COLUMNS = [
    "id",
    "documentId",
    "title",
    "postedDate",
    "receiveDate",
    "firstName",
    "lastName",
    "organization",
    "comment",
    "attachmentCount",
]

log = logging.getLogger("pull_docket_comments")


def eastern_filter_value(iso_value: str) -> str:
    """Convert an ISO-8601 lastModifiedDate to the official filter format.

    Official docs (large-docket example) use `yyyy-MM-dd HH:mm:ss` and show
    the UTC instant expressed in Eastern time.
    """
    raw = iso_value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN).strftime(LAST_MOD_FILTER_FMT)


def safe_filename(name: str) -> str:
    name = name.strip() or "file"
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name[:180]


class RegulationsClient:
    def __init__(self, base_url: str, api_key: str, min_remaining: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Api-Key": api_key,
                "Accept": "application/vnd.api+json, application/json, */*",
                "User-Agent": "regs-comments-pull/1.0",
            }
        )
        self.min_remaining = min_remaining
        self.last_limit: int | None = None
        self.last_remaining: int | None = None
        self.request_count = 0

    def _throttle(self) -> None:
        if self.last_remaining is None:
            return
        if self.last_remaining <= self.min_remaining:
            # Rolling hourly window (api.data.gov developer manual). Wait a
            # conservative slice rather than immediately retrying.
            wait = 90
            log.warning(
                "X-RateLimit-Remaining=%s (limit=%s); sleeping %ss before next request",
                self.last_remaining,
                self.last_limit,
                wait,
            )
            time.sleep(wait)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        max_retries: int = 8,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            self._throttle()
            try:
                resp = self.session.request(
                    method, url, params=params, stream=stream, timeout=60
                )
            except requests.RequestException as exc:
                last_error = exc
                sleep = min(60, 2 ** attempt)
                log.warning("Request error %s; retry in %ss", exc, sleep)
                time.sleep(sleep)
                continue

            self.request_count += 1
            limit = resp.headers.get("X-RateLimit-Limit")
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if limit is not None:
                try:
                    self.last_limit = int(limit)
                except ValueError:
                    pass
            if remaining is not None:
                try:
                    self.last_remaining = int(remaining)
                except ValueError:
                    pass
            log.info(
                "%s %s -> %s (remaining %s/%s)",
                method,
                resp.url,
                resp.status_code,
                self.last_remaining,
                self.last_limit,
            )

            if resp.status_code == 429:
                # Honor Retry-After when it is a short wait. If the header is
                # hours long (quota reset), fail immediately so the run stays
                # resumable instead of sleeping until the window opens.
                retry_after = resp.headers.get("Retry-After")
                header_seconds = None
                if retry_after:
                    try:
                        header_seconds = int(retry_after)
                    except ValueError:
                        header_seconds = None
                max_wait = 180
                if header_seconds is not None and header_seconds > max_wait:
                    raise RuntimeError(
                        f"HTTP 429 for {resp.url}; Retry-After={header_seconds}s "
                        f"({header_seconds / 3600:.1f} hours). Rate budget is exhausted. "
                        "Wait for the window to reset and rerun the same --out "
                        "(already-saved files are skipped), or set REGS_API_KEY."
                    )
                sleep = (
                    header_seconds
                    if header_seconds is not None
                    else min(120, 30 * (2 ** attempt))
                )
                log.warning("HTTP 429 rate limited; backing off %ss", sleep)
                time.sleep(sleep)
                continue

            if resp.status_code in {500, 502, 503, 504}:
                sleep = min(60, 2 ** attempt)
                log.warning("HTTP %s; retry in %ss", resp.status_code, sleep)
                time.sleep(sleep)
                continue

            return resp

        if last_error:
            raise last_error
        raise RuntimeError(f"Exceeded retries for {method} {url}")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        resp = self.request("GET", url, params=params)
        if resp.status_code == 404:
            raise FileNotFoundError(f"Not found: {resp.url}")
        if not resp.ok:
            raise RuntimeError(
                f"GET {resp.url} failed: {resp.status_code} {resp.text[:500]}"
            )
        return resp.json()

    def download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        resp = self.request("GET", url, stream=True)
        if not resp.ok:
            raise RuntimeError(f"Download {url} failed: {resp.status_code}")
        try:
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        fh.write(chunk)
            tmp.replace(dest)
        finally:
            resp.close()
            if tmp.exists():
                tmp.unlink(missing_ok=True)


def paginate_collection(
    client: RegulationsClient,
    path: str,
    base_params: dict[str, Any],
    *,
    label: str,
) -> Iterator[dict[str, Any]]:
    """Yield unique items, paging 250 x 20 then lastModifiedDate windows.

    Official comments example for >5000 records:
      sort=lastModifiedDate,documentId
      page[size]=250, page[number]=1..20
      then filter[lastModifiedDate][ge] = last record's timestamp
      (yyyy-MM-dd HH:mm:ss, Eastern in the documented example).
    """
    seen: set[str] = set()
    window_ge: str | None = None
    window = 0
    while True:
        window += 1
        last_iso: str | None = None
        pages_fetched = 0
        hit_last_page = False
        new_in_window = 0
        first_meta: dict[str, Any] | None = None

        for page in range(1, MAX_PAGE + 1):
            params = {
                **base_params,
                "page[size]": PAGE_SIZE,
                "page[number]": page,
                "sort": "lastModifiedDate,documentId",
            }
            if window_ge:
                params["filter[lastModifiedDate][ge]"] = window_ge
            payload = client.get_json(path, params)
            items = payload.get("data") or []
            meta = payload.get("meta") or {}
            if first_meta is None:
                first_meta = meta
                log.info(
                    "%s window %s page %s: totalElements=%s totalPages=%s",
                    label,
                    window,
                    page,
                    meta.get("totalElements"),
                    meta.get("totalPages"),
                )
            pages_fetched += 1
            for item in items:
                attrs = item.get("attributes") or {}
                if attrs.get("lastModifiedDate"):
                    last_iso = attrs["lastModifiedDate"]
                item_id = item.get("id")
                if not item_id or item_id in seen:
                    continue
                seen.add(item_id)
                new_in_window += 1
                yield item
            if not items or meta.get("lastPage") is True or meta.get("hasNextPage") is False:
                hit_last_page = True
                break

        log.info(
            "%s window %s done: new=%s unique_total=%s last_page=%s lastModifiedDate=%s",
            label,
            window,
            new_in_window,
            len(seen),
            hit_last_page,
            last_iso,
        )
        if hit_last_page or pages_fetched < MAX_PAGE or not last_iso:
            break
        next_ge = eastern_filter_value(last_iso)
        if window_ge == next_ge and new_in_window == 0:
            log.warning(
                "%s: lastModifiedDate window did not advance (%s); stopping to avoid a loop",
                label,
                next_ge,
            )
            break
        window_ge = next_ge
        log.info("%s: opening next window with filter[lastModifiedDate][ge]=%s", label, window_ge)


def first_page_meta(
    client: RegulationsClient, path: str, params: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = client.get_json(
        path,
        {
            **params,
            "page[size]": PAGE_SIZE,
            "page[number]": 1,
            "sort": "lastModifiedDate,documentId",
        },
    )
    return payload.get("data") or [], payload.get("meta") or {}


def iter_file_formats(node: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not node:
        return []
    attrs = node.get("attributes") if "attributes" in node else node
    if not isinstance(attrs, dict):
        return []
    formats = attrs.get("fileFormats") or []
    return [f for f in formats if isinstance(f, dict) and f.get("fileUrl")]


def collect_attachments(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Attachment fileFormats from included[] and from the comment itself."""
    files: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def add(entry: dict[str, Any], source_id: str | None = None) -> None:
        url = entry.get("fileUrl")
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        item = dict(entry)
        if source_id:
            item["_source_id"] = source_id
        files.append(item)

    data = detail.get("data") or {}
    for fmt in iter_file_formats(data):
        add(fmt, data.get("id"))
    for inc in detail.get("included") or []:
        inc_id = inc.get("id") if isinstance(inc, dict) else None
        for fmt in iter_file_formats(inc if isinstance(inc, dict) else {}):
            add(fmt, inc_id)
    return files


def comment_row(detail: dict[str, Any], attachment_count: int) -> dict[str, Any]:
    data = detail.get("data") or {}
    attrs = data.get("attributes") or {}
    return {
        "id": data.get("id") or "",
        "documentId": attrs.get("commentOnDocumentId") or attrs.get("documentId") or "",
        "title": attrs.get("title") or "",
        "postedDate": attrs.get("postedDate") or "",
        "receiveDate": attrs.get("receiveDate") or "",
        "firstName": attrs.get("firstName") or "",
        "lastName": attrs.get("lastName") or "",
        "organization": attrs.get("organization") or "",
        "comment": attrs.get("comment") or "",
        "attachmentCount": attachment_count,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def rebuild_csv_from_raw(out_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_dir = out_dir / "raw"
    if not raw_dir.exists():
        return rows
    for fp in sorted(raw_dir.glob("*.json")):
        detail = json.loads(fp.read_text(encoding="utf-8"))
        rows.append(comment_row(detail, len(collect_attachments(detail))))
    write_csv(out_dir / "comments.csv", rows)
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download regulations.gov comments for a docket (API v4)."
    )
    parser.add_argument("--docket", required=True, help="Docket ID, e.g. FDA-2025-N-3262")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API root (default: {DEFAULT_BASE_URL}). Staging: https://api-staging.regulations.gov/v4",
    )
    parser.add_argument("--out", default=None, help="Output directory (default: ./out-<docket>)")
    parser.add_argument(
        "--no-attachments",
        action="store_true",
        help="Do not request include=attachments or download files",
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=None,
        help="Fetch at most this many new comment details (resumed files do not count)",
    )
    parser.add_argument(
        "--max-attachments",
        type=int,
        default=None,
        help="Download at most this many attachment files in this run",
    )
    parser.add_argument(
        "--document-id",
        action="append",
        default=None,
        dest="document_ids",
        help="Only process this document ID (repeatable). Still lists all documents.",
    )
    parser.add_argument(
        "--open-for-comment-only",
        action="store_true",
        help="Only list/count comments on documents with openForComment=true "
        "(saves rate limit; other documents are still listed).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args(argv)
    api_key = os.environ.get("REGS_API_KEY", "").strip() or DEFAULT_KEY
    if api_key == DEFAULT_KEY:
        log.warning(
            "REGS_API_KEY is unset; using DEMO_KEY. Official docs say DEMO_KEY is "
            "for exploring the API only. api.data.gov limits DEMO_KEY to 30 "
            "requests/IP/hour and 50/IP/day. Set REGS_API_KEY for a real pull."
        )

    out_dir = Path(args.out or f"./out-{args.docket}").resolve()
    raw_dir = out_dir / "raw"
    attach_root = out_dir / "attachments"
    raw_dir.mkdir(parents=True, exist_ok=True)

    client = RegulationsClient(args.base_url, api_key)
    log.info("Base URL: %s", client.base_url)
    log.info("Output:   %s", out_dir)

    # --- Docket ---
    log.info("Fetching docket %s", args.docket)
    docket = client.get_json(f"/dockets/{args.docket}")
    (out_dir / "docket.json").write_text(json.dumps(docket, indent=2), encoding="utf-8")
    docket_attrs = (docket.get("data") or {}).get("attributes") or {}
    log.info(
        "Docket title=%r type=%s agency=%s",
        docket_attrs.get("title") or docket_attrs.get("shortTitle"),
        docket_attrs.get("docketType"),
        docket_attrs.get("agencyId"),
    )

    # --- Documents ---
    log.info("Listing documents for docket %s", args.docket)
    documents = list(
        paginate_collection(
            client,
            "/documents",
            {"filter[docketId]": args.docket},
            label=f"documents[{args.docket}]",
        )
    )
    (out_dir / "documents.json").write_text(
        json.dumps(documents, indent=2), encoding="utf-8"
    )
    for doc in documents:
        attrs = doc.get("attributes") or {}
        log.info(
            "  document id=%s type=%s objectId=%s openForComment=%s title=%r",
            doc.get("id"),
            attrs.get("documentType"),
            attrs.get("objectId"),
            attrs.get("openForComment"),
            attrs.get("title"),
        )
    if not documents:
        log.warning("No documents found for this docket.")

    # --- Comment counts + IDs ---
    counts: list[dict[str, Any]] = []
    comment_ids: list[str] = []
    seen_comment_ids: set[str] = set()
    stop_listing = False

    wanted_ids = set(args.document_ids or [])
    for doc in documents:
        attrs = doc.get("attributes") or {}
        object_id = attrs.get("objectId")
        doc_id = doc.get("id")
        if wanted_ids and doc_id not in wanted_ids:
            log.info("Skipping comment lookup for %s (--document-id filter)", doc_id)
            continue
        if args.open_for_comment_only and not attrs.get("openForComment"):
            log.info(
                "Skipping comment lookup for %s (openForComment=%s)",
                doc_id,
                attrs.get("openForComment"),
            )
            continue
        if not object_id:
            log.warning("Document %s has no objectId; skipping comment listing", doc_id)
            counts.append(
                {
                    "documentId": doc_id,
                    "documentType": attrs.get("documentType"),
                    "objectId": None,
                    "title": attrs.get("title"),
                    "totalElements": None,
                    "note": "missing objectId",
                }
            )
            continue

        # Always take page 1 to read totalElements (official large-docket example).
        first_items, meta = first_page_meta(
            client, "/comments", {"filter[commentOnId]": object_id}
        )
        total = meta.get("totalElements")
        counts.append(
            {
                "documentId": doc_id,
                "documentType": attrs.get("documentType"),
                "objectId": object_id,
                "title": attrs.get("title"),
                "totalElements": total,
                "openForComment": attrs.get("openForComment"),
            }
        )
        log.info(
            "  comments on %s (%s, objectId=%s): totalElements=%s",
            doc_id,
            attrs.get("documentType"),
            object_id,
            total,
        )

        if stop_listing:
            continue

        def consider(item: dict[str, Any]) -> None:
            cid = item.get("id")
            if not cid or cid in seen_comment_ids:
                return
            seen_comment_ids.add(cid)
            comment_ids.append(cid)

        for item in first_items:
            consider(item)

        need_more = total is None or int(total) > len(first_items)
        enough = args.max_comments is not None and len(comment_ids) >= args.max_comments
        if need_more and not enough:
            # Resume full pagination (page 1 will be deduped).
            for item in paginate_collection(
                client,
                "/comments",
                {"filter[commentOnId]": object_id},
                label=f"comments[{doc_id}]",
            ):
                consider(item)
                if args.max_comments is not None and len(comment_ids) >= args.max_comments:
                    break

        if args.max_comments is not None and len(comment_ids) >= args.max_comments:
            log.info(
                "--max-comments=%s reached while listing IDs; further documents will "
                "still be counted (page 1 only) but not fully paged",
                args.max_comments,
            )
            stop_listing = True

    (out_dir / "comment_counts.json").write_text(
        json.dumps(counts, indent=2), encoding="utf-8"
    )
    total_listed = sum(
        (c["totalElements"] or 0) for c in counts if isinstance(c.get("totalElements"), int)
    )
    log.info(
        "Documents=%s; sum of per-document totalElements=%s; comment IDs queued=%s",
        len(documents),
        total_listed,
        len(comment_ids),
    )

    # --- Comment details ---
    fetched_new = 0
    attachments_downloaded = 0
    skipped_existing = 0

    for cid in comment_ids:
        raw_path = raw_dir / f"{safe_filename(cid)}.json"
        if raw_path.exists():
            skipped_existing += 1
            log.info("Skip existing comment detail %s", cid)
            detail = json.loads(raw_path.read_text(encoding="utf-8"))
        else:
            if args.max_comments is not None and fetched_new >= args.max_comments:
                log.info("Reached --max-comments=%s; stopping detail fetches", args.max_comments)
                break
            params = {} if args.no_attachments else {"include": "attachments"}
            detail = client.get_json(f"/comments/{cid}", params)
            raw_path.write_text(json.dumps(detail, indent=2), encoding="utf-8")
            fetched_new += 1
            log.info("Saved comment detail %s", cid)

        if args.no_attachments:
            continue
        if args.max_attachments is not None and attachments_downloaded >= args.max_attachments:
            continue
        files = collect_attachments(detail)
        dest_dir = attach_root / safe_filename(cid)
        for index, fmt in enumerate(files, start=1):
            if args.max_attachments is not None and attachments_downloaded >= args.max_attachments:
                break
            url = fmt["fileUrl"]
            suffix = ""
            parsed = urlparse(url)
            base = Path(parsed.path).name or f"attachment-{index}"
            if fmt.get("format") and not base.lower().endswith(f".{fmt['format']}".lower()):
                base = f"{base}.{fmt['format']}"
            dest = dest_dir / safe_filename(base)
            if dest.exists():
                log.info("Skip existing attachment %s", dest)
                continue
            log.info("Downloading attachment %s -> %s", url, dest)
            try:
                client.download(url, dest)
                attachments_downloaded += 1
            except Exception as exc:  # noqa: BLE001 — keep going; log the failure
                log.error("Failed to download %s: %s", url, exc)

    rows = rebuild_csv_from_raw(out_dir)
    log.info(
        "Done. requests=%s new_details=%s skipped_existing=%s csv_rows=%s "
        "attachments_downloaded_this_run=%s remaining=%s/%s",
        client.request_count,
        fetched_new,
        skipped_existing,
        len(rows),
        attachments_downloaded,
        client.last_remaining,
        client.last_limit,
    )
    log.info("CSV: %s", out_dir / "comments.csv")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.error("Interrupted")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed: %s", exc)
        sys.exit(1)
