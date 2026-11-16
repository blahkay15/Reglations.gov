"""Offline tests for document selection, Windows paths, and comment download."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pull_docket_comments as pull  # noqa: E402
from pull_docket_comments import (  # noqa: E402
    ATTACHMENT_CSV_COLUMNS,
    CSV_COLUMNS,
    DOWNLOAD_USER_AGENT,
    RegulationsClient,
    eastern_filter_value,
    log,
    paginate_collection,
    parse_args,
    rebuild_csv_from_raw,
    run,
    safe_filename,
    destination_path,
)


DOCKET = "FDA-2025-N-3262"
DOC_ID = "FDA-2025-N-3262-0001"
OBJECT_ID = "0900006487ffff01"


def document(
    doc_id: str,
    doc_type: str,
    object_id: str,
    *,
    title: str = "Title",
    open_for_comment: bool = True,
    docket_id: str = DOCKET,
) -> dict:
    return {
        "id": doc_id,
        "type": "documents",
        "attributes": {
            "documentType": doc_type,
            "objectId": object_id,
            "title": title,
            "openForComment": open_for_comment,
            "docketId": docket_id,
            "lastModifiedDate": "2025-01-01T00:00:00Z",
        },
    }


def comment_detail(cid: str, *, attachments: list[dict] | None = None) -> dict:
    included = []
    if attachments:
        included.append(
            {
                "id": f"att-{cid}",
                "attributes": {"fileFormats": attachments},
            }
        )
    return {
        "data": {
            "id": cid,
            "attributes": {
                "comment": 'café, "hello"',
                "title": "A title",
                "commentOnDocumentId": DOC_ID,
                "postedDate": "2025-11-01",
                "receiveDate": "2025-10-31",
                "firstName": "Ada",
                "lastName": "Lovelace",
                "organization": "Analytical Engines",
            },
        },
        "included": included,
    }


class FakeClient:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.request_count = 0
        self.last_remaining = 9
        self.last_limit = 10
        self.detail_ids = ["C1"]
        self.attachments = [
            {
                "fileUrl": "https://downloads.regulations.gov/files/my:bad*name.pdf",
                "format": "pdf",
            }
        ]
        self.fail_downloads = False
        self.comment_pages: dict[str, list[dict]] = {}
        self.docket_documents = [
            document(DOC_ID, "Proposed Rule", OBJECT_ID, title="Proposed rule"),
            document("SUP-1", "Supporting & Related Material", "OBJ-SUP", title="Study"),
            document("OTH-1", "Other", "OBJ-OTH", title="Other doc"),
        ]
        self.fail_page_one = False

    def get_json(self, path, params=None):
        params = None if params is None else dict(params)
        self.events.append(("json", path, params))
        self.request_count += 1
        if path.startswith("/documents/") and path != "/documents":
            doc_id = path.rsplit("/", 1)[-1]
            return {"data": document(doc_id, "Proposed Rule", OBJECT_ID, title="Proposed rule")}
        if path.startswith("/dockets/"):
            docket_id = path.rsplit("/", 1)[-1]
            return {
                "data": {
                    "id": docket_id,
                    "attributes": {
                        "title": "Docket title",
                        "docketType": "Rulemaking",
                        "agencyId": "FDA",
                    },
                }
            }
        if path == "/documents":
            return {
                "data": self.docket_documents,
                "meta": {"lastPage": True, "totalElements": len(self.docket_documents)},
            }
        if path == "/comments":
            object_id = (params or {}).get("filter[commentOnId]")
            page = int((params or {}).get("page[number]", 1))
            if self.fail_page_one and page == 1:
                raise AssertionError("page 1 was fetched twice")
            pages = self.comment_pages.get(object_id)
            if pages is not None:
                return pages[page - 1]
            ids = self.detail_ids
            return {
                "data": [
                    {"id": cid, "attributes": {"lastModifiedDate": "2025-01-02T00:00:00Z"}}
                    for cid in ids
                ],
                "meta": {"totalElements": len(ids), "lastPage": True, "totalPages": 1},
            }
        if path.startswith("/comments/"):
            cid = path.rsplit("/", 1)[-1]
            return comment_detail(cid, attachments=self.attachments)
        raise AssertionError(f"unexpected path {path}")

    def download(self, url, dest: Path):
        self.events.append(("download", url, Path(dest).name))
        if self.fail_downloads:
            raise RuntimeError("simulated download failure")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"attachment-bytes")


class ParseArgsTests(unittest.TestCase):
    def test_document_id_is_enough(self):
        args = parse_args(["--document-id", DOC_ID])
        self.assertEqual(args.document_ids, [DOC_ID])
        self.assertIsNone(args.docket)
        self.assertFalse(args.all_documents)

    def test_document_id_is_repeatable_and_deduped(self):
        args = parse_args(
            ["--document-id", "A", "--document-id", "A", "--document-id", "B"]
        )
        self.assertEqual(args.document_ids, ["A", "B"])

    def test_requires_docket_or_document(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                parse_args([])
        self.assertEqual(caught.exception.code, 2)

    def test_help_describes_document_id_mode(self):
        proc = subprocess.run(
            [sys.executable, "pull_docket_comments.py", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--document-id", proc.stdout)
        self.assertIn("--all-documents", proc.stdout)
        self.assertNotIn("Still lists all documents", proc.stdout)
        self.assertNotIn("required", proc.stdout.lower())


class FilenameTests(unittest.TestCase):
    def test_strips_windows_forbidden_characters(self):
        name = safe_filename('my:bad*name?.pdf"<>|')
        for char in ':*?"<>|':
            self.assertNotIn(char, name)
        self.assertTrue(name.endswith(".pdf"))

    def test_reserved_device_name(self):
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("con.txt"), "_con.txt")

    def test_keeps_unicode_and_spaces(self):
        self.assertEqual(safe_filename("café notes.pdf"), "café notes.pdf")

    def test_caps_length_and_keeps_extension(self):
        name = safe_filename(("n" * 300) + ".pdf", max_len=40)
        self.assertLessEqual(len(name), 40)
        self.assertTrue(name.endswith(".pdf"))

    def test_destination_path_stays_short(self):
        dest = destination_path(Path("/tmp/regs"), ("n" * 300) + ".pdf", max_path=60)
        self.assertLessEqual(len(str(dest.resolve())), 60)
        self.assertTrue(dest.name.endswith(".pdf"))


class PaginationTests(unittest.TestCase):
    def test_eastern_filter_matches_official_example(self):
        self.assertEqual(
            eastern_filter_value("2020-08-10T15:58:52Z"),
            "2020-08-10 11:58:52",
        )

    def test_window_after_5000_uses_last_modified_filter(self):
        calls = []

        class PagingClient:
            def get_json(self, path, params=None):
                params = dict(params or {})
                calls.append(params)
                page = int(params["page[number]"])
                if "filter[lastModifiedDate][ge]" in params:
                    return {
                        "data": [
                            {
                                "id": "window-2",
                                "attributes": {"lastModifiedDate": "2020-08-11T00:00:00Z"},
                            }
                        ],
                        "meta": {"lastPage": True, "totalElements": 5001},
                    }
                return {
                    "data": [
                        {
                            "id": f"p{page}",
                            "attributes": {"lastModifiedDate": "2020-08-10T15:58:52Z"},
                        }
                    ],
                    "meta": {
                        "hasNextPage": True,
                        "lastPage": False,
                        "totalElements": 5001,
                        "totalPages": 21,
                    },
                }

        items = list(
            paginate_collection(
                PagingClient(),
                "/comments",
                {"filter[commentOnId]": OBJECT_ID},
                label="comments[test]",
            )
        )
        self.assertEqual(len(calls), 21)
        self.assertEqual(
            calls[-1]["filter[lastModifiedDate][ge]"],
            "2020-08-10 11:58:52",
        )
        self.assertEqual(items[-1]["id"], "window-2")

    def test_seed_does_not_refetch_page_one(self):
        calls = []

        class PagingClient:
            def get_json(self, path, params=None):
                params = dict(params or {})
                calls.append(params)
                self_page = int(params["page[number]"])
                if self_page == 1:
                    raise AssertionError("seeded page 1 was requested again")
                return {
                    "data": [
                        {"id": "C2", "attributes": {"lastModifiedDate": "2025-02-01T00:00:00Z"}}
                    ],
                    "meta": {"lastPage": True, "totalElements": 2},
                }

        seed_items = [
            {"id": "C1", "attributes": {"lastModifiedDate": "2025-01-01T00:00:00Z"}}
        ]
        seed_meta = {"hasNextPage": True, "lastPage": False, "totalElements": 2, "totalPages": 2}
        ids = [
            item["id"]
            for item in paginate_collection(
                PagingClient(),
                "/comments",
                {"filter[commentOnId]": OBJECT_ID},
                label="comments[seed]",
                seed=(seed_items, seed_meta),
            )
        ]
        self.assertEqual(ids, ["C1", "C2"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["page[number]"], 2)


class RunTests(unittest.TestCase):
    def test_document_id_resolves_object_id_and_downloads_attachments(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            handler = logging.Handler()
            handler.emit = lambda record: client.events.append(("log", record.getMessage()))
            log.addHandler(handler)
            log.setLevel(logging.INFO)
            try:
                code = run(parse_args(["--document-id", DOC_ID, "--out", str(out)]), client)
            finally:
                log.removeHandler(handler)
            self.assertEqual(code, 0)
            paths = [event[1] for event in client.events if event[0] == "json"]
            self.assertIn(f"/documents/{DOC_ID}", paths)
            self.assertNotIn("/documents", paths)
            comment_lists = [
                event for event in client.events if event[0] == "json" and event[1] == "/comments"
            ]
            self.assertEqual(len(comment_lists), 1)
            self.assertEqual(comment_lists[0][2]["filter[commentOnId]"], OBJECT_ID)
            details = [
                event
                for event in client.events
                if event[0] == "json" and str(event[1]).startswith("/comments/")
            ]
            self.assertEqual(details[0][2], {"include": "attachments"})
            plan_at = next(
                i
                for i, event in enumerate(client.events)
                if event[0] == "log" and "downloads start after this list" in event[1]
            )
            detail_at = next(
                i
                for i, event in enumerate(client.events)
                if event[0] == "json" and str(event[1]).startswith("/comments/")
            )
            self.assertLess(plan_at, detail_at)
            self.assertTrue(any(event[0] == "log" and "totalElements=1" in event[1] for event in client.events))
            saved = out / "attachments" / "C1" / "my_bad_name.pdf"
            self.assertEqual(saved.read_bytes(), b"attachment-bytes")
            raw = (out / "comments.csv").read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            with (out / "comments.csv").open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(list(rows[0].keys()), CSV_COLUMNS)
            self.assertEqual(rows[0]["id"], "C1")
            self.assertEqual(rows[0]["documentId"], DOC_ID)
            self.assertEqual(rows[0]["comment"], 'café, "hello"')
            self.assertEqual(rows[0]["attachmentCount"], "1")
            self.assertEqual(rows[0]["attachmentFiles"], r"attachments\C1\my_bad_name.pdf")
            attachment_bytes = (out / "attachments.csv").read_bytes()
            self.assertTrue(attachment_bytes.startswith(b"\xef\xbb\xbf"))
            with (out / "attachments.csv").open(encoding="utf-8-sig", newline="") as fh:
                attachment_rows = list(csv.DictReader(fh))
            self.assertEqual(list(attachment_rows[0].keys()), ATTACHMENT_CSV_COLUMNS)
            self.assertEqual(attachment_rows[0]["commentId"], "C1")
            self.assertEqual(attachment_rows[0]["documentId"], DOC_ID)
            self.assertEqual(attachment_rows[0]["fileName"], "my_bad_name.pdf")
            self.assertEqual(attachment_rows[0]["localPath"], r"attachments\C1\my_bad_name.pdf")
            self.assertEqual(attachment_rows[0]["format"], "pdf")
            self.assertEqual(
                attachment_rows[0]["sourceUrl"],
                "https://downloads.regulations.gov/files/my:bad*name.pdf",
            )
            self.assertEqual(attachment_rows[0]["downloaded"], "yes")
            self.assertEqual(attachment_rows[0]["fileSizeBytes"], str(len(b"attachment-bytes")))
            counts = json.loads((out / "comment_counts.json").read_text(encoding="utf-8"))
            self.assertEqual(counts[0]["totalElements"], 1)
            self.assertEqual(counts[0]["objectId"], OBJECT_ID)

    def test_repeatable_document_ids(self):
        client = FakeClient()
        client.detail_ids = []
        with tempfile.TemporaryDirectory() as tmp:
            run(
                parse_args(
                    [
                        "--document-id",
                        DOC_ID,
                        "--document-id",
                        "FDA-2025-N-3262-0002",
                        "--out",
                        tmp,
                        "--no-attachments",
                    ]
                ),
                client,
            )
        doc_gets = [
            event[1]
            for event in client.events
            if event[0] == "json" and str(event[1]).startswith("/documents/")
        ]
        self.assertEqual(
            doc_gets,
            [f"/documents/{DOC_ID}", "/documents/FDA-2025-N-3262-0002"],
        )

    def test_docket_defaults_to_proposed_rule(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            run(parse_args(["--docket", DOCKET, "--out", str(tmp)]), client)
            listed = json.loads((Path(tmp) / "documents.json").read_text(encoding="utf-8"))
            counts = json.loads((Path(tmp) / "comment_counts.json").read_text(encoding="utf-8"))
        self.assertEqual([doc["id"] for doc in listed], [DOC_ID, "SUP-1", "OTH-1"])
        self.assertEqual([row["documentId"] for row in counts], [DOC_ID])
        object_ids = [
            event[2]["filter[commentOnId]"]
            for event in client.events
            if event[0] == "json" and event[1] == "/comments"
        ]
        self.assertEqual(object_ids, [OBJECT_ID])

    def test_all_documents_includes_every_type(self):
        client = FakeClient()
        client.detail_ids = []
        with tempfile.TemporaryDirectory() as tmp:
            run(
                parse_args(["--docket", DOCKET, "--all-documents", "--out", tmp, "--no-attachments"]),
                client,
            )
        object_ids = [
            event[2]["filter[commentOnId]"]
            for event in client.events
            if event[0] == "json" and event[1] == "/comments"
        ]
        self.assertEqual(object_ids, [OBJECT_ID, "OBJ-SUP", "OBJ-OTH"])

    def test_totals_for_every_selected_document_print_before_max_comments_stops(self):
        client = FakeClient()
        client.docket_documents = [
            document(DOC_ID, "Proposed Rule", OBJECT_ID, title="First rule"),
            document("FDA-2025-N-3262-0002", "Proposed Rule", "OBJ-2", title="Second rule"),
            document("SUP-1", "Supporting & Related Material", "OBJ-SUP"),
        ]
        client.detail_ids = ["C1", "C2", "C3"]
        with tempfile.TemporaryDirectory() as tmp:
            run(
                parse_args(
                    ["--docket", DOCKET, "--out", tmp, "--max-comments", "1", "--no-attachments"]
                ),
                client,
            )
            counts = json.loads((Path(tmp) / "comment_counts.json").read_text(encoding="utf-8"))
        self.assertEqual([row["documentId"] for row in counts], [DOC_ID, "FDA-2025-N-3262-0002"])
        details = [
            event[1]
            for event in client.events
            if event[0] == "json" and str(event[1]).startswith("/comments/")
        ]
        self.assertEqual(details, ["/comments/C1"])

    def test_resume_skips_saved_comment_detail(self):
        client = FakeClient()
        client.attachments = []
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            raw = out / "raw"
            raw.mkdir()
            (raw / "C1.json").write_text(
                json.dumps(comment_detail("C1")),
                encoding="utf-8",
            )
            run(parse_args(["--document-id", DOC_ID, "--out", str(out)]), client)
        details = [
            event
            for event in client.events
            if event[0] == "json" and str(event[1]).startswith("/comments/")
        ]
        self.assertEqual(details, [])
        downloads = [event for event in client.events if event[0] == "download"]
        self.assertEqual(downloads, [])

    def test_duplicate_attachment_names_do_not_overwrite(self):
        client = FakeClient()
        client.attachments = [
            {"fileUrl": "https://downloads.regulations.gov/one/report.pdf", "format": "pdf"},
            {"fileUrl": "https://downloads.regulations.gov/two/report.pdf?x=2", "format": "pdf"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            run(parse_args(["--document-id", DOC_ID, "--out", str(out)]), client)
            names = sorted(p.name for p in (out / "attachments" / "C1").iterdir())
        self.assertEqual(names, ["report-2.pdf", "report.pdf"])

    def test_second_comment_page_is_requested_once(self):
        client = FakeClient()
        client.attachments = []
        client.comment_pages = {
            OBJECT_ID: [
                {
                    "data": [
                        {"id": "C1", "attributes": {"lastModifiedDate": "2025-01-01T00:00:00Z"}}
                    ],
                    "meta": {
                        "totalElements": 2,
                        "totalPages": 2,
                        "lastPage": False,
                        "hasNextPage": True,
                    },
                },
                {
                    "data": [
                        {"id": "C2", "attributes": {"lastModifiedDate": "2025-01-02T00:00:00Z"}}
                    ],
                    "meta": {"totalElements": 2, "totalPages": 2, "lastPage": True},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            run(
                parse_args(["--document-id", DOC_ID, "--out", tmp, "--no-attachments"]),
                client,
            )
        pages = [
            event[2]["page[number]"]
            for event in client.events
            if event[0] == "json" and event[1] == "/comments"
        ]
        self.assertEqual(pages, [1, 2])

    def test_document_docket_mismatch_errors(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                run(
                    parse_args(["--docket", "OTHER-DOCKET", "--document-id", DOC_ID, "--out", tmp]),
                    client,
                )

    def test_resume_counts_attachment_already_on_disk(self):
        client = FakeClient()
        detail = comment_detail("C1", attachments=client.attachments)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            raw = out / "raw"
            raw.mkdir()
            (raw / "C1.json").write_text(json.dumps(detail), encoding="utf-8")
            saved = out / "attachments" / "C1" / "my_bad_name.pdf"
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"already-there")
            run(parse_args(["--document-id", DOC_ID, "--out", str(out)]), client)
            with (out / "comments.csv").open(encoding="utf-8-sig", newline="") as fh:
                comments = list(csv.DictReader(fh))
            with (out / "attachments.csv").open(encoding="utf-8-sig", newline="") as fh:
                attachments = list(csv.DictReader(fh))
            self.assertEqual(saved.read_bytes(), b"already-there")
        downloads = [event for event in client.events if event[0] == "download"]
        self.assertEqual(downloads, [])
        self.assertEqual(comments[0]["attachmentCount"], "1")
        self.assertEqual(comments[0]["attachmentFiles"], r"attachments\C1\my_bad_name.pdf")
        self.assertEqual(attachments[0]["downloaded"], "yes")
        self.assertEqual(attachments[0]["fileSizeBytes"], str(len(b"already-there")))

    def test_rebuild_from_raw_marks_only_files_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            raw = out / "raw"
            raw.mkdir()
            kept = {
                "fileUrl": "https://downloads.regulations.gov/files/kept.pdf",
                "format": "pdf",
                "size": 10,
            }
            missing = {
                "fileUrl": "https://downloads.regulations.gov/files/missing.docx",
                "format": "docx",
                "size": 20,
            }
            (raw / "C1.json").write_text(
                json.dumps(comment_detail("C1", attachments=[kept])),
                encoding="utf-8",
            )
            (raw / "C2.json").write_text(
                json.dumps(comment_detail("C2", attachments=[missing])),
                encoding="utf-8",
            )
            dest = out / "attachments" / "C1" / "kept.pdf"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b"x" * 10)
            rebuild_csv_from_raw(out)
            with (out / "comments.csv").open(encoding="utf-8-sig", newline="") as fh:
                comments = list(csv.DictReader(fh))
            with (out / "attachments.csv").open(encoding="utf-8-sig", newline="") as fh:
                attachments = list(csv.DictReader(fh))
        by_id = {row["id"]: row for row in comments}
        self.assertEqual(by_id["C1"]["attachmentFiles"], r"attachments\C1\kept.pdf")
        self.assertEqual(by_id["C2"]["attachmentCount"], "1")
        self.assertEqual(by_id["C2"]["attachmentFiles"], "")
        self.assertEqual(
            [(row["commentId"], row["fileName"], row["downloaded"], row["fileSizeBytes"]) for row in attachments],
            [("C1", "kept.pdf", "yes", "10"), ("C2", "missing.docx", "no", "20")],
        )

    def test_max_attachments_still_lists_files_not_downloaded(self):
        client = FakeClient()
        client.attachments = [
            {
                "fileUrl": "https://downloads.regulations.gov/one/report.pdf",
                "format": "pdf",
                "size": 100,
            },
            {
                "fileUrl": "https://downloads.regulations.gov/two/report.pdf?x=2",
                "format": "pdf",
                "size": 250,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            run(
                parse_args(
                    ["--document-id", DOC_ID, "--out", str(out), "--max-attachments", "1"]
                ),
                client,
            )
            with (out / "comments.csv").open(encoding="utf-8-sig", newline="") as fh:
                comments = list(csv.DictReader(fh))
            with (out / "attachments.csv").open(encoding="utf-8-sig", newline="") as fh:
                attachments = list(csv.DictReader(fh))
        downloads = [event for event in client.events if event[0] == "download"]
        self.assertEqual([event[2] for event in downloads], ["report.pdf"])
        self.assertEqual(comments[0]["attachmentCount"], "2")
        self.assertEqual(comments[0]["attachmentFiles"], r"attachments\C1\report.pdf")
        self.assertEqual(
            [(row["fileName"], row["downloaded"], row["fileSizeBytes"], row["localPath"]) for row in attachments],
            [
                ("report.pdf", "yes", "100", r"attachments\C1\report.pdf"),
                ("report-2.pdf", "no", "250", r"attachments\C1\report-2.pdf"),
            ],
        )

    def test_failed_download_is_listed_as_not_downloaded(self):
        client = FakeClient()
        client.fail_downloads = True
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            run(parse_args(["--document-id", DOC_ID, "--out", str(out)]), client)
            with (out / "comments.csv").open(encoding="utf-8-sig", newline="") as fh:
                comments = list(csv.DictReader(fh))
            with (out / "attachments.csv").open(encoding="utf-8-sig", newline="") as fh:
                attachments = list(csv.DictReader(fh))
        self.assertEqual(comments[0]["attachmentCount"], "1")
        self.assertEqual(comments[0]["attachmentFiles"], "")
        self.assertEqual(attachments[0]["downloaded"], "no")
        self.assertEqual(attachments[0]["fileName"], "my_bad_name.pdf")
        self.assertFalse((out / "attachments" / "C1" / "my_bad_name.pdf").exists())


class _FakeFileResponse:
    def __init__(self, status_code: int, body: bytes = b"") -> None:
        self.status_code = status_code
        self.url = "https://downloads.regulations.gov/FDA-2025-N-3262-0001/content.pdf"
        self._body = body
        self.closed = False
        self.ok = 200 <= status_code < 300

    def iter_content(self, chunk_size: int = 0):
        yield self._body

    def close(self) -> None:
        self.closed = True


class DownloadSessionTests(unittest.TestCase):
    URL = "https://downloads.regulations.gov/FDA-2025-N-3262-0001/content.pdf"

    def setUp(self) -> None:
        self.client = RegulationsClient("https://api.regulations.gov/v4", "secret-key")
        # An API call in this state would sleep on the rate-limit throttle.
        self.client.last_remaining = 0
        self.client.min_remaining = 2
        self.slept: list[float] = []
        self._original_sleep = pull.time.sleep
        pull.time.sleep = lambda seconds: self.slept.append(seconds)

    def tearDown(self) -> None:
        pull.time.sleep = self._original_sleep

    def test_file_session_has_browser_user_agent_and_no_api_key(self) -> None:
        self.assertEqual(self.client.session.headers["X-Api-Key"], "secret-key")
        self.assertEqual(self.client.session.headers["User-Agent"], "regs-comments-pull/1.0")
        self.assertNotIn("X-Api-Key", self.client.download_session.headers)
        self.assertEqual(self.client.download_session.headers["User-Agent"], DOWNLOAD_USER_AGENT)
        self.assertNotIn("secret-key", " ".join(self.client.download_session.headers.values()))

    def test_403_is_not_saved_and_a_later_run_retries(self) -> None:
        calls = {"n": 0}

        def fake_get(url, stream=False, timeout=None):
            self.assertEqual(url, self.URL)
            self.assertTrue(stream)
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeFileResponse(403)
            return _FakeFileResponse(200, b"%PDF-1.4")

        def api_must_not_be_used(*args, **kwargs):
            raise AssertionError("file download used the API session")

        self.client.download_session.get = fake_get  # type: ignore[method-assign]
        self.client.session.request = api_must_not_be_used  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "content.pdf"
            with self.assertRaises(RuntimeError) as caught:
                self.client.download(self.URL, dest)
            self.assertIn("HTTP 403", str(caught.exception))
            self.assertIn("downloaded=no", str(caught.exception))
            self.assertIn("Re-run the same command", str(caught.exception))
            self.assertFalse(dest.exists())
            self.assertFalse(list(Path(tmp).glob("*.part")))
            self.client.download(self.URL, dest)
            self.assertEqual(dest.read_bytes(), b"%PDF-1.4")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self.client.request_count, 0)
        self.assertEqual(self.slept, [])


if __name__ == "__main__":
    unittest.main()
