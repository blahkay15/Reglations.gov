"""ECS entrypoint. Local dev sets REGS_LOCAL=1 and does not use this module's AWS clients."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _client():
    from api.preview_live import api_key_from_secret
    from regs_pull.engine import DEFAULT_BASE_URL, RegulationsClient

    if os.environ.get("REGS_LOCAL") == "1":
        key = os.environ.get("REGS_API_KEY", "")
        if os.environ.get("REGS_FIXTURE", "1") == "1" or not key:
            from api.fixture import FixtureClient

            return FixtureClient()
        return RegulationsClient(os.environ.get("REGS_BASE_URL", DEFAULT_BASE_URL), key)

    import boto3

    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["API_KEY_SECRET_ARN"])
    key = api_key_from_secret(secret.get("SecretString") or "")
    return RegulationsClient(os.environ.get("REGS_BASE_URL", DEFAULT_BASE_URL), key)


def main() -> int:
    from api.repo import DiskRepo
    from worker.run_job import execute_job

    job = {
        "jobId": os.environ["JOB_ID"],
        "userId": os.environ["USER_ID"],
        "documentId": os.environ["DOCUMENT_ID"],
    }
    if os.environ.get("REGS_LOCAL") == "1":
        repo = DiskRepo(Path(os.environ.get("REGS_LOCAL_DIR", "var/local")))
    else:
        from api.aws_repo import AwsRepo

        repo = AwsRepo()
    out_dir = Path(tempfile.mkdtemp(prefix=f"job-{job['jobId']}-"))
    execute_job(job, _client(), repo, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
