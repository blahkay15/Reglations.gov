#!/usr/bin/env python3
"""Install Lambda dependencies and copy the pipeline sources into build/lambda/pipeline.

Terraform zips that directory. Run this before terraform plan or apply.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "build" / "lambda" / "pipeline"


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"), "-t", str(DEST)],
    )
    source = ROOT / "src" / "backend" / "pipeline"
    for path in source.glob("*.py"):
        shutil.copy(path, DEST / path.name)
    shutil.copytree(
        ROOT / "regs_pull",
        DEST / "regs_pull",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    print(DEST)


if __name__ == "__main__":
    main()
