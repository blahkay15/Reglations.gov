"""Form-letter clustering."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from regs_pull.cluster import ClusterConfig, cluster_comments, cluster_summaries  # noqa: E402

FORM = (
    "I strongly support this proposed rule because it protects patients "
    "and I urge the agency to finalize it without further delay today"
)
UNIQUE_A = "Section 4.2 defines device too broadly for audit-only software. Please add an example that excludes log files."
UNIQUE_B = "The 60-day comment window overlapped our accreditation survey. A two-week extension would produce better data."


class ClusterTests(unittest.TestCase):
    def test_identical_letters_cluster_and_unique_comments_do_not(self):
        comments = [
            {"id": "a", "text": FORM},
            {"id": "b", "text": FORM + " Thank you."},
            {"id": "c", "text": FORM},
            {"id": "d", "text": FORM.replace("strongly ", "")},
            {"id": "e", "text": UNIQUE_A},
            {"id": "f", "text": UNIQUE_B},
        ]
        config = ClusterConfig(threshold=0.5, min_cluster_size=3, num_perm=32, bands=8)
        assignments = cluster_comments(comments, config)
        by_id = {item["id"]: item for item in assignments}
        self.assertTrue(by_id["a"]["isFormLetter"])
        self.assertEqual(by_id["a"]["clusterId"], by_id["b"]["clusterId"])
        self.assertEqual(by_id["a"]["clusterId"], by_id["c"]["clusterId"])
        self.assertFalse(by_id["e"]["isFormLetter"])
        self.assertFalse(by_id["f"]["isFormLetter"])
        self.assertEqual(by_id["e"]["clusterId"], "")
        self.assertNotEqual(by_id["e"]["clusterId"], by_id["a"]["clusterId"])
        summaries = cluster_summaries(comments, assignments, config)
        self.assertEqual(len(summaries), 1)
        self.assertGreaterEqual(summaries[0]["count"], 3)
        self.assertIn("proposed rule", summaries[0]["representativeText"].lower())

    def test_pair_below_min_size_is_not_a_form_letter(self):
        comments = [{"id": "a", "text": FORM}, {"id": "b", "text": FORM}]
        assignments = cluster_comments(comments, ClusterConfig(min_cluster_size=3, num_perm=16, bands=4))
        self.assertTrue(all(not item["isFormLetter"] for item in assignments))

    def test_threshold_is_configurable(self):
        comments = [
            {"id": "a", "text": FORM},
            {"id": "b", "text": "Completely unrelated text about parking permits and bicycle racks downtown."},
            {"id": "c", "text": FORM},
        ]
        loose = cluster_comments(comments, ClusterConfig(threshold=0.99, min_cluster_size=2, num_perm=16, bands=4))
        by_id = {item["id"]: item for item in loose}
        self.assertNotEqual(by_id["a"]["clusterId"], by_id["b"]["clusterId"] if by_id["b"]["isFormLetter"] else "nope")
        self.assertFalse(by_id["b"]["isFormLetter"])
