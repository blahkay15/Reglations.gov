"""Build the files the web app serves after a pull finishes.

The command-line CSV stays as the puller wrote it. The app adds
``clusterId`` and ``isFormLetter`` in a second pass so existing CLI tests
keep the original columns.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from regs_pull.cluster import ClusterConfig, cluster_comments, cluster_summaries
from regs_pull.engine import ATTACHMENT_CSV_COLUMNS, CSV_COLUMNS, write_csv

APP_COMMENT_COLUMNS = [*CSV_COLUMNS, "clusterId", "isFormLetter"]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _posted_day(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text[:10] if text else "Unknown"


def enrich_output(out_dir: Path, config: ClusterConfig | None = None) -> dict[str, Any]:
    """Rewrite comments.csv with cluster columns and write summary files."""
    out_dir = Path(out_dir)
    rows = _read_csv(out_dir / "comments.csv")
    attachments = _read_csv(out_dir / "attachments.csv")
    assignments = cluster_comments(
        [{"id": row.get("id", ""), "text": row.get("comment", "")} for row in rows],
        config,
    )
    by_id = {str(item["id"]): item for item in assignments}
    enriched: list[dict[str, Any]] = []
    for row in rows:
        info = by_id.get(row.get("id", ""), {})
        enriched.append(
            {
                **row,
                "clusterId": info.get("clusterId") or "",
                "isFormLetter": "yes" if info.get("isFormLetter") else "no",
                "commentUrl": f"https://www.regulations.gov/comment/{row.get('id', '')}",
            }
        )
    write_csv(
        out_dir / "comments.csv",
        [{key: row.get(key, "") for key in APP_COMMENT_COLUMNS} for row in enriched],
        APP_COMMENT_COLUMNS,
    )

    summaries = cluster_summaries(
        [{"id": row.get("id", ""), "text": row.get("comment", "")} for row in rows],
        assignments,
        config,
    )
    by_day = Counter(_posted_day(row.get("postedDate", "")) for row in enriched)
    organizations = Counter((row.get("organization") or "").strip() or "Not specified" for row in enriched)
    formats = Counter((row.get("format") or "").strip() or "unknown" for row in attachments)
    downloaded = sum(1 for row in attachments if row.get("downloaded") == "yes")
    summary = {
        "commentCount": len(enriched),
        "formLetterCount": sum(1 for row in enriched if row["isFormLetter"] == "yes"),
        "uniqueCount": sum(1 for row in enriched if row["isFormLetter"] != "yes"),
        "attachmentCount": len(attachments),
        "attachmentsDownloaded": downloaded,
        "commentsOverTime": [
            {"date": day, "count": count}
            for day, count in sorted(by_day.items())
        ],
        "topOrganizations": [
            {"name": name, "count": count}
            for name, count in organizations.most_common(8)
        ],
        "attachmentTypes": [
            {"format": fmt, "count": count}
            for fmt, count in formats.most_common()
        ],
        "clusters": summaries,
    }
    index = [
        {
            "id": row.get("id", ""),
            "documentId": row.get("documentId", ""),
            "title": row.get("title", ""),
            "postedDate": row.get("postedDate", ""),
            "organization": row.get("organization", ""),
            "comment": row.get("comment", ""),
            "attachmentCount": row.get("attachmentCount", "0"),
            "attachmentFiles": row.get("attachmentFiles", ""),
            "clusterId": row.get("clusterId", ""),
            "isFormLetter": row.get("isFormLetter", "no"),
            "commentUrl": row.get("commentUrl", ""),
        }
        for row in enriched
    ]
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out_dir / "comments_index.json").write_text(json.dumps(index) + "\n", encoding="utf-8")
    return summary
