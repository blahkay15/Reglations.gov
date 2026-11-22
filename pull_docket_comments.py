#!/usr/bin/env python3
"""Command-line entry point.

The puller itself lives in :mod:`regs_pull` so the web worker can import it.
Running this file behaves the same as before.
"""

from __future__ import annotations

import sys

from regs_pull.engine import (
    ATTACHMENT_CSV_COLUMNS,
    CSV_COLUMNS,
    DOWNLOAD_USER_AGENT,
    DEFAULT_BASE_URL,
    DEFAULT_KEY,
    RegulationsClient,
    destination_path,
    eastern_filter_value,
    log,
    main,
    paginate_collection,
    parse_args,
    rebuild_csv_from_raw,
    run,
    safe_filename,
)

__all__ = [
    "ATTACHMENT_CSV_COLUMNS",
    "CSV_COLUMNS",
    "DEFAULT_BASE_URL",
    "DEFAULT_KEY",
    "DOWNLOAD_USER_AGENT",
    "RegulationsClient",
    "destination_path",
    "eastern_filter_value",
    "log",
    "main",
    "paginate_collection",
    "parse_args",
    "rebuild_csv_from_raw",
    "run",
    "safe_filename",
]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.error("Interrupted")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed: %s", exc)
        sys.exit(1)
