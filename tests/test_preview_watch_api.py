"""Preview estimates, watcher silence, and API behavior."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.fixture import FixtureClient  # noqa: E402
from api.handler import route  # noqa: E402
from api.repo import MemoryRepo  # noqa: E402
from api.watcher import check_watch  # noqa: E402
from regs_pull.preview import estimate_calls, preview_document  # noqa: E402
from regs_pull.watch import post_if_new, summarize_new_comments  # noqa: E402
from worker.run_job import execute_job  # noqa: E402


class PreviewTests(unittest.TestCase):
    def test_estimate_counts_detail_calls(self):
        # 1 document lookup + 2 list pages + 300 details.
        self.assertEqual(estimate_calls(300), 303)

    def test_fixture_preview_warns_only_when_not_a_proposed_rule(self):
        preview = preview_document(FixtureClient(), "FDA-2025-N-3262-0001", rate_per_hour=1000)
        self.assertEqual(preview["documentType"], "Proposed Rule")
        self.assertTrue(preview["proposedRule"])
        self.assertIsNone(preview["warning"])
        self.assertEqual(preview["totalComments"], 6)
        self.assertGreater(preview["estimatedHours"], 0)
        self.assertIn("FDA-2025-N-3262-0001", preview["regulationsGovUrl"])


class WatchTests(unittest.TestCase):
    def test_nothing_new_does_not_post(self):
        summary = summarize_new_comments("FDA-2025-N-3262-0001", [])
        self.assertTrue(summary["silent"])
        posts = []
        self.assertFalse(post_if_new("https://example.test/hook", summary, lambda url, body: posts.append(body)))
        self.assertEqual(posts, [])

    def test_new_comments_post_a_card_and_split_form_letters(self):
        form = "Please finalize this proposed rule as written to protect patients and communities nationwide today."
        comments = [
            {"id": "n1", "text": form},
            {"id": "n2", "text": form},
            {"id": "n3", "text": form},
            {"id": "n4", "text": "The software log exemption in proposed 3.1(b) needs a worked example."},
        ]
        summary = summarize_new_comments("FDA-2025-N-3262-0001", comments, app_url="https://comments.example")
        posts = []
        self.assertTrue(post_if_new("https://example.test/hook", summary, lambda url, body: posts.append((url, body))))
        self.assertEqual(summary["newCount"], 4)
        self.assertGreaterEqual(summary["formLetterCount"], 3)
        self.assertEqual(summary["uniqueCount"], summary["newCount"] - summary["formLetterCount"])
        card = posts[0][1]["attachments"][0]["content"]
        self.assertEqual(card["type"], "AdaptiveCard")
        self.assertIn("FDA-2025-N-3262-0001", card["body"][0]["text"])

    def test_seen_ids_are_not_reported_again(self):
        listed = [{"id": "a", "text": "one"}, {"id": "b", "text": "two"}]
        posts = []
        summary = check_watch(
            {"documentId": "DOC-1", "webhookUrl": "https://example.test/hook", "seenIds": ["a"]},
            listed,
            poster=lambda url, body: posts.append(body),
        )
        self.assertEqual(summary["newCount"], 1)
        self.assertTrue(summary["posted"])
        self.assertEqual(summary["seenIds"], ["a", "b"])
        again = check_watch(
            {"documentId": "DOC-1", "webhookUrl": "https://example.test/hook", "seenIds": summary["seenIds"]},
            listed,
            poster=lambda url, body: posts.append(body),
        )
        self.assertTrue(again["silent"])
        self.assertEqual(len(posts), 1)


class ApiTests(unittest.TestCase):
    def test_job_flow_hides_webhook_and_serves_results(self):
        repo = MemoryRepo()
        status, preview = route(
            "GET",
            "/api/preview",
            {"documentId": "FDA-2025-N-3262-0001"},
            None,
            "user-1",
            repo,
            previewer=lambda document_id: preview_document(FixtureClient(), document_id),
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["totalComments"], 6)

        status, job = route(
            "POST",
            "/api/jobs",
            {},
            {"documentId": "FDA-2025-N-3262-0001", "title": preview["title"]},
            "user-1",
            repo,
        )
        self.assertEqual(status, 202)
        self.assertEqual(repo.started[0]["documentId"], "FDA-2025-N-3262-0001")
        with tempfile.TemporaryDirectory() as tmp:
            execute_job(job, FixtureClient(), repo, Path(tmp))
        status, saved = route("GET", f"/api/jobs/{job['jobId']}", {}, None, "user-1", repo)
        self.assertEqual(saved["status"], "succeeded")
        self.assertGreater(saved["fetched"], 0)

        status, summary = route("GET", f"/api/jobs/{job['jobId']}/summary", {}, None, "user-1", repo)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(summary["formLetterCount"], 3)
        self.assertGreaterEqual(len(summary["commentsOverTime"]), 1)

        status, page = route(
            "GET",
            f"/api/jobs/{job['jobId']}/comments",
            {"q": "software", "pageSize": "10"},
            None,
            "user-1",
            repo,
        )
        self.assertEqual(page["total"], 1)
        self.assertIn("regulations.gov/comment/", page["comments"][0]["commentUrl"])

        status, form_page = route(
            "GET",
            f"/api/jobs/{job['jobId']}/comments",
            {"form": "yes"},
            None,
            "user-1",
            repo,
        )
        self.assertGreaterEqual(form_page["total"], 3)

        status, download = route(
            "GET",
            f"/api/jobs/{job['jobId']}/download",
            {"kind": "comments"},
            None,
            "user-1",
            repo,
        )
        self.assertEqual(status, 200)
        csv_bytes = repo.get_object(download["key"])
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        header = csv_bytes.decode("utf-8-sig").splitlines()[0]
        self.assertIn("clusterId", header)
        self.assertIn("isFormLetter", header)

        status, other = route("GET", f"/api/jobs/{job['jobId']}", {}, None, "someone-else", repo)
        self.assertEqual(status, 404)
        self.assertIsNone(other.get("jobId") if isinstance(other, dict) else None)

        status, watch = route(
            "POST",
            "/api/watches",
            {},
            {"documentId": "FDA-2025-N-3262-0001", "webhookUrl": "https://example.test/webhook/secret"},
            "user-1",
            repo,
        )
        self.assertEqual(status, 201)
        self.assertNotIn("webhookUrl", watch)
        self.assertTrue(watch["webhookConfigured"])
        listed = route("GET", "/api/watches", {}, None, "user-1", repo)[1]
        self.assertNotIn("https://example.test/webhook/secret", json.dumps(listed))
        stored = repo.get_watch("user-1", "FDA-2025-N-3262-0001")
        self.assertEqual(stored["webhookUrl"], "https://example.test/webhook/secret")


if __name__ == "__main__":
    unittest.main()
