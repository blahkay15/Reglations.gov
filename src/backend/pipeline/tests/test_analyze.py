from __future__ import annotations

import unittest

from analyze import analyze, groups_from_comments
from storage import MemoryStore, job_key

FORM = (
    "I strongly support this proposed rule because it protects patients "
    "and I urge the agency to finalize it without further delay today"
)


def _comment(comment_id: str, text: str, organization: str) -> dict:
    return {
        "id": comment_id,
        "documentId": "FDA-1",
        "comment": text,
        "organization": organization,
        "title": comment_id,
        "postedDate": "2025-01-02",
    }


class AnalyzeTests(unittest.TestCase):
    def test_form_letters_cluster_and_bedrock_is_capped(self):
        store = MemoryStore()
        comments = [
            _comment("a", FORM, "Patients United"),
            _comment("b", FORM, "Patients United"),
            _comment("c", FORM, "Patients United"),
            _comment("d", "Section 4 should exclude audit-only software logs.", "City Hospital"),
        ]
        for comment in comments:
            store.put_json(job_key("job-1", comment["id"]), comment)

        def labeler(batch):
            return [
                {
                    "theme": "support the rule" if item["isFormLetter"] else "software logs",
                    "stance": "support" if item["isFormLetter"] else "oppose",
                    "commenterType": "advocacy" if item["isFormLetter"] else "industry",
                    "summary": item["text"][:40],
                }
                for item in batch
            ]

        summary = analyze({"jobId": "job-1", "documentId": "FDA-1", "maxLabeledClusters": 1}, store, labeler)
        self.assertEqual(summary["commentCount"], 4)
        self.assertEqual(summary["labeledFindings"], 1)
        self.assertGreaterEqual(summary["formLetterComments"], 3)
        self.assertEqual(summary["findings"][0]["theme"], "support the rule")
        self.assertEqual(store.get_job("job-1")["status"], "SUCCEEDED")
        self.assertEqual(len(store.findings("job-1")), 1)

    def test_groups_mark_three_copies_as_a_form_letter(self):
        groups = groups_from_comments(
            [
                _comment("a", FORM, "Org"),
                _comment("b", FORM, "Org"),
                _comment("c", FORM, "Org"),
            ]
        )
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["isFormLetter"])
        self.assertEqual(groups[0]["clusterSize"], 3)
