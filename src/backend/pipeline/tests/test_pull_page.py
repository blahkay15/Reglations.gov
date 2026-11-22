from __future__ import annotations

import unittest

from pull_page import RateLimited, handle
from storage import MemoryStore


class FakeClient:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("HTTP 429 for comments; Retry-After=30s")
        if path.startswith("/documents/"):
            return {
                "data": {
                    "id": path.rsplit("/", 1)[-1],
                    "attributes": {"objectId": "OBJ-1", "title": "Proposed rule", "documentType": "Proposed Rule"},
                }
            }
        if path == "/comments":
            return {
                "data": [
                    {"id": "C1", "attributes": {"lastModifiedDate": "2025-01-02T00:00:00Z"}},
                    {"id": "C2", "attributes": {"lastModifiedDate": "2025-01-01T00:00:00Z"}},
                ],
                "meta": {"totalElements": 2, "totalPages": 1, "lastPage": True},
            }
        if path.startswith("/comments/"):
            comment_id = path.rsplit("/", 1)[-1]
            return {
                "data": {
                    "id": comment_id,
                    "attributes": {
                        "comment": "Please finalize the rule.",
                        "commentOnDocumentId": "FDA-1",
                        "organization": "Acme Corp",
                        "title": comment_id,
                        "postedDate": "2025-01-02",
                        "lastModifiedDate": "2025-01-02T00:00:00Z",
                    },
                }
            }
        raise AssertionError(path)


class PullPageTests(unittest.TestCase):
    def test_plan_counts_pages_from_total_elements(self):
        store = MemoryStore()
        state = handle(
            {"mode": "plan", "jobId": "job-1", "documentId": "FDA-1", "pageSize": 20},
            FakeClient(),
            store,
        )
        self.assertEqual(state["objectId"], "OBJ-1")
        self.assertEqual(state["pages"], [1])
        self.assertEqual(state["totalComments"], 2)
        self.assertFalse(state["done"])
        self.assertEqual(store.get_job("job-1")["status"], "RUNNING")

    def test_page_writes_details_and_skips_existing(self):
        store = MemoryStore()
        client = FakeClient()
        event = {"mode": "page", "jobId": "job-1", "objectId": "OBJ-1", "page": 1, "pageSize": 20}
        first = handle(event, client, store)
        second = handle(event, client, store)
        self.assertEqual(first["written"], 2)
        self.assertEqual(second["written"], 0)
        self.assertEqual(second["skipped"], 2)
        saved = store.list_comments("job-1")
        self.assertEqual(saved[0]["organization"], "Acme Corp")

    def test_rate_limit_is_a_named_error_for_step_functions(self):
        store = MemoryStore()
        with self.assertRaises(RateLimited):
            handle(
                {"mode": "page", "jobId": "job-1", "objectId": "OBJ-1", "page": 1, "pageSize": 20},
                FakeClient(fail_on="/comments/"),
                store,
            )

    def test_next_window_stops_when_every_comment_is_saved(self):
        store = MemoryStore()
        handle({"mode": "plan", "jobId": "job-1", "documentId": "FDA-1", "pageSize": 20}, FakeClient(), store)
        handle({"mode": "page", "jobId": "job-1", "objectId": "OBJ-1", "page": 1, "pageSize": 20}, FakeClient(), store)
        state = handle(
            {
                "mode": "next_window",
                "jobId": "job-1",
                "documentId": "FDA-1",
                "objectId": "OBJ-1",
                "totalComments": 2,
                "loops": 0,
                "pageSize": 20,
            },
            FakeClient(),
            store,
        )
        self.assertTrue(state["done"])
        self.assertEqual(state["pages"], [])
