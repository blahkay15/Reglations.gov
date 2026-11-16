#!/usr/bin/env python3
"""Build the Lambda asset directory. Run this before cdk synth or cdk deploy."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "lambda"


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--upgrade", "-r", str(ROOT / "requirements.txt"), "-t", str(OUTPUT)],
    )
    for name in ("regs_pull", "api", "worker"):
        shutil.copytree(ROOT / name, OUTPUT / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Lambda asset: {OUTPUT}")


if __name__ == "__main__":
    main()
