"""Who can see a job, and who can change it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.handler import Actor, actor_from_claims, route  # noqa: E402
from api.repo import MemoryRepo  # noqa: E402

ALICE = Actor(user_id="alice", email="alice@example.com", name="Alice Adams")
BOB = Actor(user_id="bob", email="bob@example.com", name="Bob Baker")
ADMIN = Actor(user_id="admin", email="admin@example.com", name="Ada Admin", admin=True)


def _call(repo, actor, method, path, body=None, query=None):
    return route(method, path, query or {}, body, actor.user_id, repo, actor=actor)


class AccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = MemoryRepo()
        status, self.job = _call(
            self.repo,
            ALICE,
            "POST",
            "/api/jobs",
            {"documentId": "FDA-2025-N-3262-0001", "title": "Alice's pull"},
        )
        self.assertEqual(status, 202)
        status, self.watch = _call(
            self.repo,
            ALICE,
            "POST",
            "/api/watches",
            {"documentId": "FDA-2025-N-3262-0001", "webhookUrl": "https://example.test/alice"},
        )
        self.assertEqual(status, 201)

    def test_identity_is_recorded(self):
        self.assertEqual(self.job["userId"], "alice")
        self.assertEqual(self.job["ownerName"], "Alice Adams")
        self.assertEqual(self.job["ownerEmail"], "alice@example.com")
        self.assertTrue(self.job["createdAt"])
        self.assertEqual(self.watch["ownerName"], "Alice Adams")
        self.assertNotIn("webhookUrl", self.watch)
        self.assertNotIn("https://example.test/alice", str(self.watch))

    def test_mine_and_team_lists(self):
        _call(self.repo, BOB, "POST", "/api/jobs", {"documentId": "CMS-2024-0001-0001"})
        mine = _call(self.repo, ALICE, "GET", "/api/jobs", query={"scope": "mine"})[1]["jobs"]
        team = _call(self.repo, BOB, "GET", "/api/jobs", query={"scope": "team"})[1]["jobs"]
        self.assertEqual([job["userId"] for job in mine], ["alice"])
        self.assertEqual({job["userId"] for job in team}, {"alice", "bob"})
        watches = _call(self.repo, BOB, "GET", "/api/watches", query={"scope": "team"})[1]["watches"]
        self.assertEqual(watches[0]["ownerEmail"], "alice@example.com")
        self.assertFalse(watches[0]["canManage"])

    def test_any_signed_in_user_can_read_a_job_but_not_cancel_it(self):
        status, seen = _call(self.repo, BOB, "GET", f"/api/jobs/{self.job['jobId']}")
        self.assertEqual(status, 200)
        self.assertFalse(seen["canManage"])
        status, denied = _call(self.repo, BOB, "DELETE", f"/api/jobs/{self.job['jobId']}")
        self.assertEqual(status, 403)
        self.assertEqual(self.repo.find_job(self.job["jobId"])["status"], "queued")

    def test_owner_and_admin_can_cancel_or_delete(self):
        status, cancelled = _call(self.repo, ALICE, "DELETE", f"/api/jobs/{self.job['jobId']}")
        self.assertEqual(status, 200)
        self.assertEqual(cancelled["status"], "cancelled")
        finished = {
            **self.job,
            "jobId": "done123",
            "status": "succeeded",
            "scope": "team",
        }
        self.repo.create_job("alice", finished)
        status, removed = _call(self.repo, ADMIN, "DELETE", "/api/jobs/done123")
        self.assertEqual(status, 200)
        self.assertEqual(removed["deleted"], "done123")
        self.assertIsNone(self.repo.find_job("done123"))

    def test_only_owner_or_admin_can_edit_or_delete_a_watch(self):
        status, denied = _call(
            self.repo,
            BOB,
            "PATCH",
            "/api/watches/alice/FDA-2025-N-3262-0001",
            {"webhookUrl": "https://example.test/bob"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(
            self.repo.get_watch("alice", "FDA-2025-N-3262-0001")["webhookUrl"],
            "https://example.test/alice",
        )
        status, updated = _call(
            self.repo,
            ADMIN,
            "PATCH",
            "/api/watches/alice/FDA-2025-N-3262-0001",
            {"webhookUrl": "https://example.test/replaced"},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("webhookUrl", updated)
        self.assertEqual(
            self.repo.get_watch("alice", "FDA-2025-N-3262-0001")["webhookUrl"],
            "https://example.test/replaced",
        )
        status, _gone = _call(self.repo, BOB, "DELETE", "/api/watches/alice/FDA-2025-N-3262-0001")
        self.assertEqual(status, 403)
        status, _deleted = _call(self.repo, ALICE, "DELETE", "/api/watches/FDA-2025-N-3262-0001")
        self.assertEqual(status, 200)
        self.assertIsNone(self.repo.get_watch("alice", "FDA-2025-N-3262-0001"))

    def test_admin_group_comes_from_the_token(self):
        member = actor_from_claims(
            {"sub": "u1", "email": "a@example.com", "name": "A", "cognito:groups": ["regs-admins"]}
        )
        self.assertTrue(member.admin)
        stranger = actor_from_claims({"sub": "u2", "email": "b@example.com", "name": "B", "cognito:groups": "readers"})
        self.assertFalse(stranger.admin)
        entra = actor_from_claims(
            {"sub": "u3", "email": "c@example.com", "name": "C", "custom:groups": ["group-object-id"]},
            admin_group_id="group-object-id",
        )
        self.assertTrue(entra.admin)
        self.assertEqual(entra.email, "c@example.com")
        self.assertEqual(entra.name, "C")
