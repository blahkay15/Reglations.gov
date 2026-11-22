#!/usr/bin/env python3
"""Invoke the dev AgentCore Runtime from a laptop.

Environment:
  AGENT_RUNTIME_ARN   Required. Terraform output agent_runtime_arn.
  AWS_REGION          Optional. Uses the AWS CLI default region when unset.
  AGENT_SESSION_ID    Optional. Must be at least 33 characters.
  PROMPT              Optional. Defaults to a short analyst question.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import boto3


def main() -> int:
    agent_arn = os.environ.get("AGENT_RUNTIME_ARN")
    if not agent_arn:
        print("AGENT_RUNTIME_ARN is required", file=sys.stderr)
        return 1

    session = boto3.Session()
    region = os.environ.get("AWS_REGION") or session.region_name
    if not region:
        print("AWS_REGION is required, or set a default region in the AWS CLI", file=sys.stderr)
        return 1

    prompt = os.environ.get("PROMPT", "How many form letters were found, and what are the top themes?")
    if not isinstance(prompt, str) or not prompt.strip():
        print("PROMPT must be a non-empty string", file=sys.stderr)
        return 1

    # The runtime requires runtimeSessionId to be at least 33 characters.
    session_id = os.environ.get("AGENT_SESSION_ID") or f"dev-session-{uuid.uuid4()}"
    if len(session_id) < 33:
        print("AGENT_SESSION_ID must be at least 33 characters", file=sys.stderr)
        return 1

    client = session.client("bedrock-agentcore", region_name=region)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )
    body = response.get("response") or response.get("body")
    if hasattr(body, "read"):
        content = body.read()
        text = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else str(content)
        print(text)
    else:
        print(json.dumps(response, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
