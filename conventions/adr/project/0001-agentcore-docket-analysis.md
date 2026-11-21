# ADR 0001: AgentCore for the docket-analysis demo

## Status

Accepted for the dev demo. Not a production decision.

## Context

Blake is showing Amazon Bedrock AgentCore to engineers. The story has two parts: start a long comment analysis, then ask questions about the stored result. A large proposed rule takes hours because the regulations.gov key allows about 1,000 calls an hour and each comment is another call.

The repo already has a Fargate worker behind the web app. That path is the right one when a single process must run for hours and hold files on disk. This demo needs a visible orchestrator and a hosted agent, and it must stay out of the production web stack.

Copilot Studio can host a chat, but the tools would not live next to the Python clustering code, and the team would not see Step Functions, Lambda, or AgentCore.

## Decision

- The chat agent runs on AgentCore Runtime as a Strands process in an ARM64 container. Its tools are ordinary Python functions that call AWS with boto3. There is no AgentCore Gateway and no tool Lambda.
- The analysis itself is a Step Functions Standard state machine and exactly two Python 3.12 Lambdas. One Lambda fetches a page of comments. The other clusters near-duplicates with the existing MinHash code and asks Bedrock to label themes. Step Functions retries rate-limit errors and can run for hours.
- Storage is one S3 bucket, one DynamoDB table, and one Secrets Manager secret. Terraform creates the secret with no value.

## Consequences

- A page of comment details, and the final labeling step, must finish within Lambda's 15 minute and 10 GB limits. If a real docket exceeds that, move that step to the existing Fargate worker and leave the state machine in charge of retries. That is the scale-up path. It is not built here.
- The web app's CDK stack is unchanged. This demo is a separate Terraform root and is valid only when `environment` is `dev`.
- Model access, the AgentCore runtime ARN shape, and a few IAM action names are marked `# VERIFY` in the Terraform. Confirm them in the account before the live demo.
