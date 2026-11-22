#!/usr/bin/env python3
"""CDK app. Account and region come from CDK_DEFAULT_ACCOUNT and CDK_DEFAULT_REGION."""

from __future__ import annotations

import os

import aws_cdk as cdk

from regs_stack import RegsStack

app = cdk.App()
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
region = os.environ.get("CDK_DEFAULT_REGION", "us-east-1")
environment = cdk.Environment(account=account, region=region) if account else cdk.Environment(region=region)
RegsStack(
    app,
    "RegsComments",
    env=environment,
    description="Regulations.gov comment workspace: CloudFront, Cognito, HTTP API, Fargate pulls.",
)
app.synth()
