"""A tiny stand-in for regulations.gov so local dev does not spend API calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any


FORM = (
    "I strongly support this proposed rule because it protects patients "
    "and I urge the agency to finalize it without delay."
)


class FixtureClient:
    """Enough of RegulationsClient for one proposed-rule document."""

    def __init__(self) -> None:
        self.request_count = 0
        self.last_remaining = 1000
        self.last_limit = 1000
        self.base_url = "fixture://regulations"

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.request_count += 1
        params = params or {}
        if path.startswith("/documents/"):
            document_id = path.rsplit("/", 1)[-1]
            return {
                "data": {
                    "id": document_id,
                    "attributes": {
                        "title": "Example proposed rule for local development",
                        "agencyId": "FDA",
                        "documentType": "Proposed Rule",
                        "docketId": "FDA-2025-N-3262",
                        "objectId": "fixture-object",
                        "openForComment": True,
                        "commentStartDate": "2025-11-01T00:00:00Z",
                        "commentEndDate": "2026-01-15T00:00:00Z",
                        "postedDate": "2025-11-01",
                    },
                }
            }
        if path.startswith("/dockets/"):
            return {"data": {"id": path.rsplit("/", 1)[-1], "attributes": {"title": "Example docket", "agencyId": "FDA"}}}
        if path == "/comments" and "page[number]" in params and int(params["page[number]"]) > 1:
            return {"data": [], "meta": {"totalElements": 6, "lastPage": True}}
        if path == "/comments":
            return {
                "data": [
                    {"id": f"C{index}", "attributes": {"lastModifiedDate": "2025-11-02T00:00:00Z"}}
                    for index in range(1, 7)
                ],
                "meta": {"totalElements": 6, "lastPage": True, "totalPages": 1},
            }
        if path.startswith("/comments/"):
            comment_id = path.rsplit("/", 1)[-1]
            texts = {
                "C1": FORM,
                "C2": FORM + " Thank you.",
                "C3": FORM,
                "C4": FORM.replace("strongly ", ""),
                "C5": "The definition of device in section 3 is ambiguous for software that only logs data. Please clarify with an example.",
                "C6": "Our hospital's comment period overlapped a holiday. A 30-day extension would let clinicians respond.",
            }
            organizations = {
                "C1": "Patients United",
                "C2": "Patients United",
                "C3": "Patients United",
                "C4": "Civic Template Org",
                "C5": "City General Hospital",
                "C6": "City General Hospital",
            }
            included = []
            if comment_id == "C5":
                included.append(
                    {
                        "id": "att-c5",
                        "attributes": {
                            "fileFormats": [
                                {
                                    "fileUrl": "https://downloads.regulations.gov/files/note.pdf",
                                    "format": "pdf",
                                    "size": 12,
                                }
                            ]
                        },
                    }
                )
            return {
                "data": {
                    "id": comment_id,
                    "attributes": {
                        "comment": texts.get(comment_id, FORM),
                        "title": "Comment " + comment_id,
                        "commentOnDocumentId": "FDA-2025-N-3262-0001",
                        "postedDate": f"2025-11-0{min(int(comment_id[1:]), 9)}",
                        "organization": organizations.get(comment_id, ""),
                        "firstName": "Local",
                        "lastName": "User",
                    },
                },
                "included": included,
            }
        raise FileNotFoundError(path)

    def download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-fixture")
