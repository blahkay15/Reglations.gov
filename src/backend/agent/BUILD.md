# Build the ARM64 agent image

AgentCore Runtime pulls a linux/arm64 image. From the repo root in PowerShell, after the ECR repository exists:

```powershell
$account = aws sts get-caller-identity --query Account --output text
$region = "us-east-1"
$repo = "$account.dkr.ecr.$region.amazonaws.com/regs-docket-dev-agent"
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin "$account.dkr.ecr.$region.amazonaws.com"
docker buildx build --platform linux/arm64 -f src/backend/agent/Dockerfile -t "$repo:latest" --push .
```

Change `regs-docket-dev-agent` if `project` or `environment` in `dev.tfvars` is different. The full order, including the first apply that creates the repository before this push, is in `docs/agentcore-demo/DEPLOY.md`.
