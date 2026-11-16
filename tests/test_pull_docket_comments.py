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

from pull_docket_comments import (  # noqa: E402
    eastern_filter_value,
    log,
    paginate_collection,
    parse_args,
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
            self.assertEqual(rows[0]["id"], "C1")
            self.assertEqual(rows[0]["comment"], 'café, "hello"')
            self.assertEqual(rows[0]["attachmentCount"], "1")
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


if __name__ == "__main__":
    unittest.main()
