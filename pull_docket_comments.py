#!/usr/bin/env python3
"""Pull public comments for a regulations.gov document via official API v4.

Primary path (one or more document IDs):
  1. GET /v4/documents/{documentId} and read attributes.objectId
  2. GET /v4/comments?filter[commentOnId]={objectId}
     page[size]=250, pages 1-20; if more than 5000 comments, continue with
     sort=lastModifiedDate,documentId and filter[lastModifiedDate][ge]=...
  3. GET /v4/comments/{commentId}?include=attachments for text and files

Docket path (--docket, no --document-id):
  list documents, keep documentType "Proposed Rule" unless --all-documents,
  then the same comment steps. Comment totals are printed before downloads.

List endpoints do not include comment body; detail does (OpenAPI Comment vs
CommentDetail). commentOnId is the parent document's objectId, not documentId.
Docs: https://open.gsa.gov/api/regulationsgov/
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
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo

import requests

DEFAULT_BASE_URL = "https://api.regulations.gov/v4"
DEFAULT_KEY = "DEMO_KEY"
# downloads.regulations.gov returns 403 for non-browser user agents, including
# python-requests and this script's API user agent. The file host does not use
# the API key.
DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
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
    "attachmentFiles",
]

ATTACHMENT_CSV_COLUMNS = [
    "commentId",
    "documentId",
    "fileName",
    "localPath",
    "format",
    "sourceUrl",
    "downloaded",
    "fileSizeBytes",
]

log = logging.getLogger("pull_docket_comments")

# Windows forbids <>:"/\|?* and ASCII controls in file names, plus trailing
# dots/spaces and device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9).
_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
PROPOSED_RULE = "Proposed Rule"
# Stay under the legacy Windows MAX_PATH (260) with room for a ".part" suffix.
MAX_PATH_LEN = 230


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


def safe_filename(name: str, *, max_len: int = 120) -> str:
    """Return one path segment that is legal on Windows and POSIX.

    Characters Windows forbids (<>:"/\\|?* and control characters) are
    replaced. Unicode letters, spaces, and other allowed punctuation stay.
    """
    text = _FORBIDDEN_CHARS.sub("_", str(name or ""))
    text = re.sub(r"_+", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    if not text or text in {".", ".."}:
        text = "file"
    stem = text.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        text = f"_{text}"
    max_len = max(12, max_len)
    if len(text) > max_len:
        dot = text.rfind(".")
        if 0 < dot < len(text) - 1:
            ext = text[dot + 1 :][:20]
            keep = max_len - len(ext) - 1
            root = text[:dot].strip(" .") or "file"
            text = root[:max_len] if keep < 1 else f"{root[:keep]}.{ext}"
        else:
            text = text[:max_len]
        text = text.rstrip(" ._") or "file"
        if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            text = f"_{text}"
    return text


def destination_path(directory: Path, filename: str, *, max_path: int = MAX_PATH_LEN) -> Path:
    """Join directory and filename, shortening the name if the full path is long."""
    directory = Path(directory)
    name = safe_filename(filename)
    dest = directory / name

    def length_of(path: Path) -> int:
        try:
            return len(str(path.resolve()))
        except OSError:
            return len(str(path))

    extra = length_of(dest) - max_path
    if extra <= 0:
        return dest
    shortened = safe_filename(name, max_len=max(12, len(name) - extra))
    dest = directory / shortened
    if length_of(dest) > max_path:
        log.warning("Path is still long (%s characters): %s", length_of(dest), dest)
    return dest


def unique_filename(filename: str, used: set[str]) -> str:
    """Pick a deterministic unused name. `used` stores lowercased names."""
    candidate = safe_filename(filename)
    dot = candidate.rfind(".")
    if 0 < dot < len(candidate) - 1:
        root, ext = candidate[:dot], candidate[dot:]
    else:
        root, ext = candidate, ""
    number = 2
    while candidate.lower() in used:
        candidate = safe_filename(f"{root}-{number}{ext}")
        number += 1
        if number > 9999:
            break
    return candidate


def attachment_basename(fmt: dict[str, Any], index: int) -> str:
    """Basename from a fileUrl, without using pathlib (Windows treats ':' specially)."""
    url = str(fmt.get("fileUrl") or "")
    tail = unquote(urlparse(url).path.rsplit("/", 1)[-1]).strip()
    base = tail or f"attachment-{index}"
    ext = str(fmt.get("format") or "").strip().lstrip(".").lower()
    if ext and not base.lower().endswith("." + ext):
        base = f"{base}.{ext}"
    return base


def api_segment(value: str) -> str:
    return quote(str(value), safe="-._")


def csv_local_path(*parts: str) -> str:
    """Relative path for the CSVs.

    Always backslash-separated so Windows Excel can open the path, including
    when the script itself ran on another operating system.
    """
    return "\\".join(parts)


def assign_attachment_destinations(
    comment_id: str, files: list[dict[str, Any]], attach_root: Path
) -> list[tuple[dict[str, Any], Path]]:
    """Map each attachment to the same local path the downloader uses."""
    dest_dir = attach_root / safe_filename(comment_id)
    used_names: set[str] = set()
    assigned: list[tuple[dict[str, Any], Path]] = []
    for index, fmt in enumerate(files, start=1):
        if not fmt.get("fileUrl"):
            continue
        chosen = unique_filename(attachment_basename(fmt, index), used_names)
        dest = destination_path(dest_dir, chosen)
        if dest.name.lower() in used_names:
            dest = destination_path(dest_dir, unique_filename(dest.name, used_names))
        used_names.add(dest.name.lower())
        assigned.append((fmt, dest))
    return assigned


def file_size_bytes(fmt: dict[str, Any], dest: Path) -> str:
    """API `size` when present, otherwise the on-disk size, otherwise blank."""
    size = fmt.get("size")
    if isinstance(size, bool):
        size = None
    if isinstance(size, int):
        return str(size)
    if isinstance(size, float) and size >= 0 and size.is_integer():
        return str(int(size))
    if isinstance(size, str) and size.strip().isdigit():
        return size.strip()
    if dest.is_file():
        try:
            return str(dest.stat().st_size)
        except OSError:
            return ""
    return ""


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
        # Separate from the API session so file hosts never see X-Api-Key and
        # file GETs do not consume the API rate-limit budget.
        self.download_session = requests.Session()
        self.download_session.headers.clear()
        self.download_session.headers.update(
            {
                "User-Agent": DOWNLOAD_USER_AGENT,
                "Accept": "*/*",
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

    def _get_file(self, url: str, max_retries: int = 4) -> requests.Response:
        """GET a file. Does not use the API session, key, or rate-limit throttle."""
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                resp = self.download_session.get(url, stream=True, timeout=60)
            except requests.RequestException as exc:
                last_error = exc
                sleep = min(30, 2 ** attempt)
                log.warning("Download error %s; retry in %ss", exc, sleep)
                time.sleep(sleep)
                continue
            log.info(
                "GET %s -> %s (file download, not counted against the API limit)",
                resp.url,
                resp.status_code,
            )
            if resp.status_code == 403:
                resp.close()
                raise RuntimeError(
                    f"HTTP 403 downloading {url}. The file host refused the download, "
                    "so the file was not saved (downloaded=no). "
                    "Re-run the same command to retry this file. This is not an API rate limit."
                )
            if resp.status_code in {500, 502, 503, 504}:
                resp.close()
                sleep = min(30, 2 ** attempt)
                log.warning(
                    "HTTP %s downloading %s; retry in %ss",
                    resp.status_code,
                    url,
                    sleep,
                )
                time.sleep(sleep)
                continue
            return resp
        if last_error:
            raise last_error
        raise RuntimeError(f"Exceeded retries downloading {url}")

    def download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Keep the original suffix. Path.with_suffix would replace ".tar.gz"
        # with a single suffix and is easier to get wrong on Windows.
        tmp = dest.parent / (dest.name + ".part")
        resp = self._get_file(url)
        if not resp.ok:
            status = resp.status_code
            resp.close()
            raise RuntimeError(f"Download {url} failed: {status}")
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
    seed: tuple[list[dict[str, Any]], dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield unique items, paging 250 x 20 then lastModifiedDate windows.

    Official comments example for >5000 records:
      sort=lastModifiedDate,documentId
      page[size]=250, page[number]=1..20
      then filter[lastModifiedDate][ge] = last record's timestamp
      (yyyy-MM-dd HH:mm:ss, Eastern in the documented example).

    `seed` is an already-fetched page 1 (items, meta) so a totals lookup is
    not requested again.
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
            if seed is not None and window == 1 and page == 1:
                items, meta = seed
            else:
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


def comment_document_id(detail: dict[str, Any]) -> str:
    data = detail.get("data") or {}
    attrs = data.get("attributes") or {}
    return str(attrs.get("commentOnDocumentId") or attrs.get("documentId") or "")


def comment_row(detail: dict[str, Any], downloaded_paths: list[str]) -> dict[str, Any]:
    data = detail.get("data") or {}
    attrs = data.get("attributes") or {}
    return {
        "id": data.get("id") or "",
        "documentId": comment_document_id(detail),
        "title": attrs.get("title") or "",
        "postedDate": attrs.get("postedDate") or "",
        "receiveDate": attrs.get("receiveDate") or "",
        "firstName": attrs.get("firstName") or "",
        "lastName": attrs.get("lastName") or "",
        "organization": attrs.get("organization") or "",
        "comment": attrs.get("comment") or "",
        "attachmentCount": len(collect_attachments(detail)),
        "attachmentFiles": "; ".join(downloaded_paths),
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    """UTF-8 with BOM so Excel on Windows opens the file as Unicode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def attachment_rows_for(detail: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """One CSV row per known attachment. Files already on disk count as downloaded."""
    data = detail.get("data") or {}
    comment_id = str(data.get("id") or "")
    document_id = comment_document_id(detail)
    folder = safe_filename(comment_id)
    rows: list[dict[str, Any]] = []
    for fmt, dest in assign_attachment_destinations(
        comment_id, collect_attachments(detail), out_dir / "attachments"
    ):
        on_disk = dest.is_file()
        rows.append(
            {
                "commentId": comment_id,
                "documentId": document_id,
                "fileName": dest.name,
                "localPath": csv_local_path("attachments", folder, dest.name),
                "format": fmt.get("format") or "",
                "sourceUrl": fmt.get("fileUrl") or "",
                "downloaded": "yes" if on_disk else "no",
                "fileSizeBytes": file_size_bytes(fmt, dest),
            }
        )
    return rows


def rebuild_csv_from_raw(out_dir: Path) -> list[dict[str, Any]]:
    """Rewrite comments.csv and attachments.csv from raw JSON plus files on disk."""
    rows: list[dict[str, Any]] = []
    attachment_rows: list[dict[str, Any]] = []
    raw_dir = out_dir / "raw"
    if not raw_dir.exists():
        return rows
    for fp in sorted(raw_dir.glob("*.json")):
        detail = json.loads(fp.read_text(encoding="utf-8"))
        planned = attachment_rows_for(detail, out_dir)
        attachment_rows.extend(planned)
        downloaded = [row["localPath"] for row in planned if row["downloaded"] == "yes"]
        rows.append(comment_row(detail, downloaded))
    write_csv(out_dir / "comments.csv", rows, CSV_COLUMNS)
    write_csv(out_dir / "attachments.csv", attachment_rows, ATTACHMENT_CSV_COLUMNS)
    return rows


def doc_attrs(doc: dict[str, Any]) -> dict[str, Any]:
    attrs = doc.get("attributes") or {}
    return attrs if isinstance(attrs, dict) else {}


def is_proposed_rule(doc: dict[str, Any]) -> bool:
    return str(doc_attrs(doc).get("documentType") or "").strip().casefold() == PROPOSED_RULE.casefold()


def describe_types(documents: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for doc in documents:
        label = str(doc_attrs(doc).get("documentType") or "unknown")
        counts[label] = counts.get(label, 0) + 1
    if not counts:
        return "none"
    parts = [f"{count} {name}" for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    return ", ".join(parts)


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def page_covers_all(items: list[dict[str, Any]], meta: dict[str, Any]) -> bool:
    """True when page 1 already holds every row (no further comment pages)."""
    total = as_int(meta.get("totalElements"))
    if not items:
        return total in (None, 0)
    if meta.get("lastPage") is True or meta.get("hasNextPage") is False:
        return True
    total_pages = as_int(meta.get("totalPages"))
    if total is not None and len(items) >= total:
        return True
    if total_pages is not None and total_pages <= 1:
        return True
    return False


def dedupe_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def fetch_document(client: RegulationsClient, document_id: str) -> dict[str, Any]:
    path = f"/documents/{api_segment(document_id)}"
    try:
        payload = client.get_json(path)
    except FileNotFoundError:
        raise RuntimeError(
            f"Document {document_id} was not found (GET /v4/documents/{document_id})."
        ) from None
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict) or not data.get("id"):
        raise RuntimeError(f"Unexpected response for GET /v4/documents/{document_id}")
    return data


def choose_out_dir(args: argparse.Namespace, documents: list[dict[str, Any]]) -> Path:
    if args.out:
        return Path(args.out)
    if args.docket:
        return Path("out-" + safe_filename(args.docket))
    docket_ids: list[str] = []
    for doc in documents:
        docket_id = str(doc_attrs(doc).get("docketId") or "")
        if docket_id and docket_id not in docket_ids:
            docket_ids.append(docket_id)
    if len(docket_ids) == 1:
        return Path("out-" + safe_filename(docket_ids[0]))
    if len(documents) == 1 and documents[0].get("id"):
        return Path("out-" + safe_filename(str(documents[0]["id"])))
    return Path("out-comments")


def save_dockets(client: RegulationsClient, out_dir: Path, docket_ids: list[str]) -> None:
    saved: list[tuple[str, dict[str, Any]]] = []
    for docket_id in docket_ids:
        log.info("Fetching docket %s", docket_id)
        try:
            payload = client.get_json(f"/dockets/{api_segment(docket_id)}")
        except FileNotFoundError:
            log.warning("Docket %s was not found; continuing without docket metadata", docket_id)
            continue
        attrs = (payload.get("data") or {}).get("attributes") or {}
        log.info(
            "Docket title=%r type=%s agency=%s",
            attrs.get("title") or attrs.get("shortTitle"),
            attrs.get("docketType"),
            attrs.get("agencyId"),
        )
        saved.append((docket_id, payload))
    if len(saved) == 1:
        write_json(out_dir / "docket.json", saved[0][1])
        return
    for docket_id, payload in saved:
        write_json(out_dir / f"docket-{safe_filename(docket_id)}.json", payload)


def docket_ids_for(args: argparse.Namespace, documents: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    if args.docket:
        found.append(args.docket)
    for doc in documents:
        docket_id = str(doc_attrs(doc).get("docketId") or "")
        if docket_id and docket_id not in found:
            found.append(docket_id)
    return found


def select_documents(
    client: RegulationsClient, args: argparse.Namespace
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (every document we learned about, documents whose comments we will pull)."""
    if args.document_ids and args.all_documents:
        log.warning("--all-documents has no effect when --document-id is used")

    if args.document_ids:
        log.info(
            "Resolving %s document id(s) via GET /v4/documents/{id}",
            len(args.document_ids),
        )
        fetched: list[dict[str, Any]] = []
        for document_id in args.document_ids:
            doc = fetch_document(client, document_id)
            attrs = doc_attrs(doc)
            log.info(
                "  %s | %s | docket=%s | objectId=%s | %s",
                doc.get("id"),
                attrs.get("documentType"),
                attrs.get("docketId"),
                attrs.get("objectId"),
                attrs.get("title"),
            )
            docket_id = str(attrs.get("docketId") or "")
            if args.docket and docket_id and docket_id != args.docket:
                raise RuntimeError(
                    f"Document {doc.get('id')} is in docket {docket_id}, not {args.docket}."
                )
            fetched.append(doc)
        selected = filter_open_for_comment(fetched, args.open_for_comment_only)
        return fetched, selected

    log.info("Listing documents for docket %s", args.docket)
    listed = list(
        paginate_collection(
            client,
            "/documents",
            {"filter[docketId]": args.docket},
            label=f"documents[{args.docket}]",
        )
    )
    log.info(
        "Docket %s has %s document(s): %s",
        args.docket,
        len(listed),
        describe_types(listed),
    )
    if not listed:
        log.warning("No documents found for this docket.")
    if args.all_documents:
        candidates = list(listed)
        log.info("--all-documents: pulling every document type (%s)", len(candidates))
    else:
        candidates = [doc for doc in listed if is_proposed_rule(doc)]
        log.info(
            "Pulling Proposed Rule documents only (%s of %s). "
            "Pass --all-documents to include every type, or pass --document-id for a specific document.",
            len(candidates),
            len(listed),
        )
    return listed, filter_open_for_comment(candidates, args.open_for_comment_only)


def filter_open_for_comment(
    documents: list[dict[str, Any]], open_only: bool
) -> list[dict[str, Any]]:
    if not open_only:
        return list(documents)
    selected: list[dict[str, Any]] = []
    for doc in documents:
        if doc_attrs(doc).get("openForComment"):
            selected.append(doc)
            continue
        log.info(
            "Skipping %s (openForComment=%s)",
            doc.get("id"),
            doc_attrs(doc).get("openForComment"),
        )
    return selected


def comment_plan(client: RegulationsClient, doc: dict[str, Any]) -> dict[str, Any]:
    attrs = doc_attrs(doc)
    document_id = doc.get("id")
    object_id = attrs.get("objectId")
    plan: dict[str, Any] = {
        "documentId": document_id,
        "documentType": attrs.get("documentType"),
        "objectId": object_id,
        "title": attrs.get("title"),
        "openForComment": attrs.get("openForComment"),
        "totalElements": None,
        "first_items": [],
        "meta": {},
        "note": None,
    }
    if not object_id:
        plan["note"] = "missing objectId"
        log.warning("Document %s has no objectId; its comments cannot be listed", document_id)
        return plan
    items, meta = first_page_meta(client, "/comments", {"filter[commentOnId]": object_id})
    plan["first_items"] = items
    plan["meta"] = meta
    plan["totalElements"] = meta.get("totalElements")
    return plan


def public_count(plan: dict[str, Any]) -> dict[str, Any]:
    row = {
        "documentId": plan.get("documentId"),
        "documentType": plan.get("documentType"),
        "objectId": plan.get("objectId"),
        "title": plan.get("title"),
        "totalElements": plan.get("totalElements"),
        "openForComment": plan.get("openForComment"),
    }
    if plan.get("note"):
        row["note"] = plan["note"]
    return row


def announce_plan(plans: list[dict[str, Any]]) -> None:
    log.info(
        "Will pull comments for %s document(s). "
        "Totals below are meta.totalElements; downloads start after this list:",
        len(plans),
    )
    if not plans:
        log.info("  (no documents)")
        return
    known_total = 0
    all_known = True
    for plan in plans:
        count = plan.get("totalElements")
        count_int = as_int(count)
        if count_int is None:
            all_known = False
            count_text = "unknown" if count is None else str(count)
        else:
            known_total += count_int
            count_text = str(count_int)
        note = f" ({plan['note']})" if plan.get("note") else ""
        log.info(
            "  %s | %s | totalElements=%s | objectId=%s | %s%s",
            plan.get("documentId"),
            plan.get("documentType"),
            count_text,
            plan.get("objectId"),
            plan.get("title") or "",
            note,
        )
    if plans and all_known:
        log.info("Sum of totalElements=%s", known_total)


def iter_comment_items(client: RegulationsClient, plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    document_id = plan.get("documentId")
    object_id = plan.get("objectId")
    items = plan.get("first_items") or []
    meta = plan.get("meta") or {}
    if not object_id:
        return
    label = f"comments[{document_id}]"
    params = {"filter[commentOnId]": object_id}
    if not items and as_int(meta.get("totalElements")) not in (None, 0):
        log.warning(
            "Page 1 for %s was empty but totalElements=%s; listing from scratch",
            document_id,
            meta.get("totalElements"),
        )
        yield from paginate_collection(client, "/comments", params, label=label)
        return
    if page_covers_all(items, meta):
        yield from items
        return
    yield from paginate_collection(client, "/comments", params, label=label, seed=(items, meta))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download regulations.gov comments for a document or docket (API v4). "
            "The primary way to run this is --document-id."
        )
    )
    parser.add_argument(
        "--docket",
        default=None,
        help=(
            "Docket ID, e.g. FDA-2025-N-3262. Pulls Proposed Rule documents only "
            "unless --all-documents is set. Optional when --document-id is given."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API root (default: {DEFAULT_BASE_URL}). Staging: https://api-staging.regulations.gov/v4",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: ./out-<docket>, from --docket or the document's docketId)",
    )
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
        metavar="DOCUMENT_ID",
        help=(
            "Document ID to pull, e.g. FDA-2025-N-3262-0001 (repeatable). "
            "Resolved with GET /v4/documents/{id}; comments and attachments for "
            "that document are downloaded. This is the primary way to run the script."
        ),
    )
    parser.add_argument(
        "--all-documents",
        action="store_true",
        help="With --docket, pull every document type. The default is Proposed Rule only.",
    )
    parser.add_argument(
        "--open-for-comment-only",
        action="store_true",
        help="Only pull documents with openForComment=true.",
    )
    args = parser.parse_args(argv)
    if args.docket:
        args.docket = args.docket.strip() or None
    args.document_ids = dedupe_ids(
        [item.strip() for item in (args.document_ids or []) if item and item.strip()]
    )
    if not args.docket and not args.document_ids:
        parser.error("provide --document-id and/or --docket")
    return args


def run(args: argparse.Namespace, client: RegulationsClient) -> int:
    listed, selected = select_documents(client, args)
    out_dir = choose_out_dir(args, selected or listed).resolve()
    raw_dir = out_dir / "raw"
    attach_root = out_dir / "attachments"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log.info("Output:   %s", out_dir)

    save_dockets(client, out_dir, docket_ids_for(args, selected or listed))
    write_json(out_dir / "documents.json", listed)

    log.info("Reading per-document comment totals before any download")
    plans = [comment_plan(client, doc) for doc in selected]
    announce_plan(plans)
    write_json(out_dir / "comment_counts.json", [public_count(plan) for plan in plans])

    comment_ids: list[str] = []
    seen_comment_ids: set[str] = set()
    stop_listing = False

    def consider(item: dict[str, Any]) -> None:
        cid = item.get("id")
        if not cid or str(cid) in seen_comment_ids:
            return
        seen_comment_ids.add(str(cid))
        comment_ids.append(str(cid))

    for plan in plans:
        if stop_listing or not plan.get("objectId"):
            continue
        for item in iter_comment_items(client, plan):
            consider(item)
            if args.max_comments is not None and len(comment_ids) >= args.max_comments:
                log.info(
                    "--max-comments=%s reached while listing IDs; remaining documents "
                    "were counted above but their comments will not be downloaded in this run",
                    args.max_comments,
                )
                stop_listing = True
                break

    log.info("Comment IDs queued for download=%s", len(comment_ids))

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
            params = None if args.no_attachments else {"include": "attachments"}
            detail = client.get_json(f"/comments/{api_segment(cid)}", params)
            write_json(raw_path, detail)
            fetched_new += 1
            log.info("Saved comment detail %s", cid)

        if args.no_attachments:
            continue
        if args.max_attachments is not None and attachments_downloaded >= args.max_attachments:
            continue
        for fmt, dest in assign_attachment_destinations(
            cid, collect_attachments(detail), attach_root
        ):
            if args.max_attachments is not None and attachments_downloaded >= args.max_attachments:
                break
            url = fmt.get("fileUrl")
            if not url:
                continue
            if dest.is_file():
                log.info("Skip existing attachment %s", dest)
                continue
            log.info("Downloading attachment %s -> %s", url, dest)
            try:
                client.download(str(url), dest)
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
    log.info("CSV: %s", out_dir / "attachments.csv")
    return 0


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
    client = RegulationsClient(args.base_url, api_key)
    log.info("Base URL: %s", client.base_url)
    return run(args, client)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.error("Interrupted")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed: %s", exc)
        sys.exit(1)
