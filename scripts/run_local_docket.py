#!/usr/bin/env python3
"""Run the pull and clustering steps on a tiny fake docket. No AWS calls.

From the repo root:

    py scripts/run_local_docket.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "backend" / "pipeline"))

from analyze import analyze  # noqa: E402
from pull_page import handle  # noqa: E402
from storage import MemoryStore  # noqa: E402

FORM = (
    "I strongly support this proposed rule because it protects patients "
    "and I urge the agency to finalize it without further delay today"
)


class LocalClient:
    def get_json(self, path, params=None):
        if path.startswith("/documents/"):
            return {
                "data": {
                    "id": "FDA-2025-N-3262-0001",
                    "attributes": {
                        "objectId": "OBJ-LOCAL",
                        "title": "Local sample proposed rule",
                        "documentType": "Proposed Rule",
                    },
                }
            }
        if path == "/comments":
            return {
                "data": [{"id": comment_id, "attributes": {}} for comment_id in ("a", "b", "c", "d", "e")],
                "meta": {"totalElements": 5, "lastPage": True},
            }
        comment_id = path.rsplit("/", 1)[-1]
        texts = {
            "a": FORM,
            "b": FORM,
            "c": FORM + " Thank you.",
            "d": "The device definition in section 3 should exclude software that only stores logs.",
            "e": "Please extend the comment period by 30 days so hospitals can respond.",
        }
        orgs = {
            "a": "Patients United",
            "b": "Patients United",
            "c": "Patients United",
            "d": "MedDevice Inc",
            "e": "City General Hospital",
        }
        return {
            "data": {
                "id": comment_id,
                "attributes": {
                    "comment": texts[comment_id],
                    "organization": orgs[comment_id],
                    "commentOnDocumentId": "FDA-2025-N-3262-0001",
                    "title": comment_id,
                    "postedDate": "2025-11-02",
                    "lastModifiedDate": "2025-11-02T00:00:00Z",
                },
            }
        }


def main() -> int:
    store = MemoryStore()
    client = LocalClient()
    state = handle(
        {"mode": "plan", "jobId": "local", "documentId": "FDA-2025-N-3262-0001", "pageSize": 20},
        client,
        store,
    )
    for page in state["pages"]:
        handle(
            {
                "mode": "page",
                "jobId": "local",
                "objectId": state["objectId"],
                "page": page,
                "pageSize": 20,
            },
            client,
            store,
        )

    def labeler(batch):
        labeled = []
        for item in batch:
            industry = "inc" in str(item.get("organization") or "").lower() or "hospital" in str(item.get("text") or "").lower()
            labeled.append(
                {
                    "theme": "form support" if item["isFormLetter"] else "technical or process",
                    "stance": "support" if item["isFormLetter"] else "mixed",
                    "commenterType": "industry" if industry else "advocacy",
                    "summary": str(item.get("text") or "")[:80],
                }
            )
        return labeled

    summary = analyze({"jobId": "local", "documentId": "FDA-2025-N-3262-0001"}, store, labeler)
    print(f"comments: {summary['commentCount']}")
    print(f"form-letter copies in labeled findings: {summary['formLetterComments']}")
    print(f"unique comments: {summary['uniqueComments']}")
    for finding in summary["findings"]:
        kind = "form letter" if finding["isFormLetter"] else "unique"
        print(f"- {kind} x{finding['clusterSize']}: {finding['theme']} ({finding['commenterType']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
