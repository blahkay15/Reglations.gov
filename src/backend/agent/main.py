"""Strands agent on AgentCore Runtime.

Tools are local Python functions that call Step Functions and DynamoDB.
There is no AgentCore Gateway and no tool Lambda.
"""

from __future__ import annotations

import json
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

from prompt import SYSTEM_PROMPT
from tools import get_findings, job_status, start_analysis

app = BedrockAgentCoreApp()


def _model() -> BedrockModel:
    # VERIFY: the account must have this inference profile enabled in this Region.
    return BedrockModel(
        model_id=os.environ.get("MODEL_ID", "us.amazon.nova-lite-v1:0"),
        temperature=0.0,
        streaming=False,
    )


@tool
def start_analysis_tool(document_id: str) -> str:
    """Start a long-running analysis of one regulations.gov document id. Returns a job id."""
    return json.dumps(start_analysis(document_id))


@tool
def job_status_tool(job_id: str) -> str:
    """Read the status of an analysis job. Use this before quoting findings."""
    return json.dumps(job_status(job_id), default=str)


@tool
def get_findings_tool(
    job_id: str = "",
    document_id: str = "",
    commenter_type: str = "",
    stance: str = "",
    theme: str = "",
    form_letters_only: bool = False,
) -> str:
    """Return labeled themes. Filter with commenter_type, stance, theme, or form_letters_only."""
    return json.dumps(
        get_findings(
            job_id=job_id,
            document_id=document_id,
            commenter_type=commenter_type,
            stance=stance,
            theme=theme,
            form_letters_only=form_letters_only,
        ),
        default=str,
    )


@app.entrypoint
def agent_invocation(payload, _context=None):
    payload = payload or {}
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "Invalid input: 'prompt' must be a non-empty string"}
    agent = Agent(
        model=_model(),
        tools=[start_analysis_tool, job_status_tool, get_findings_tool],
        system_prompt=SYSTEM_PROMPT,
    )
    result = agent(prompt.strip())
    message = getattr(result, "message", result)
    return {"result": message}


if __name__ == "__main__":
    app.run()
