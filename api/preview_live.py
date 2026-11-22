"""Preview a document with the key stored in Secrets Manager.

Imported by the Lambda only. Unit tests inject their own preview function
and never call this module.
"""

from __future__ import annotations

import json
import os

from regs_pull.engine import DEFAULT_BASE_URL, RegulationsClient
from regs_pull.preview import preview_document


def api_key_from_secret(secret_text: str) -> str:
    text = (secret_text or "").strip()
    if not text:
        raise RuntimeError("The regulations.gov API key secret is empty.")
    if text.startswith("{"):
        payload = json.loads(text)
        key = payload.get("apiKey") or payload.get("REGS_API_KEY") or payload.get("key")
        if not key:
            raise RuntimeError("The API key secret JSON needs an apiKey field.")
        return str(key).strip()
    return text


def live_preview(document_id: str) -> dict:
    import boto3

    secret_id = os.environ["API_KEY_SECRET_ARN"]
    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
    key = api_key_from_secret(response.get("SecretString") or "")
    client = RegulationsClient(os.environ.get("REGS_BASE_URL", DEFAULT_BASE_URL), key)
    rate = int(os.environ.get("REGS_RATE_PER_HOUR", "1000"))
    return preview_document(client, document_id, rate_per_hour=rate)
