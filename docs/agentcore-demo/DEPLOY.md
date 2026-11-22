# Deploy the AgentCore docket demo (Windows)

This is a **dev** stack. It is not the web app, and it is not for production. Do the steps in order. Each one says what it creates and how to check it.

You need:

- PowerShell
- Python 3.12 (`py --version`)
- Terraform 1.11 or newer (`terraform version`)
- AWS CLI v2, signed in with your **dev** SSO profile
- Docker Desktop, with the engine running

Set the profile once per window:

```powershell
$env:AWS_PROFILE = "your-dev-sso-profile"
$env:AWS_REGION = "us-east-1"
aws sts get-caller-identity
```

If that command fails, the rest will fail. Fix sign-in before continuing.

The names below assume `project = "regs-docket"` and `environment = "dev"` from `terraform/environments/dev.tfvars`. The account id comes from AWS, not from a file.

## 1. Bootstrap: state bucket, lock table, deploy role

**What and why**

- **S3** holds Terraform's memory of what it created. Versioning means you can recover a bad state file.
- **DynamoDB** is the lock so two people cannot apply at the same time.
- **IAM OIDC** lets GitHub Actions become this role later without a saved access key. You can skip using the role for now and deploy from the laptop.

```powershell
cd terraform\bootstrap
terraform init
terraform apply -var="project=regs-docket" -var="environment=dev" -var="aws_region=us-east-1" -var="github_repository=blahkay15/Reglations.gov" -var="github_ref=refs/heads/main"
```

Write down the three outputs: `state_bucket`, `lock_table`, `github_deploy_role_arn`.

**Check:** `aws s3 ls` shows the state bucket. The bucket name ends with your account id.

**Common error:** `BucketAlreadyExists` means that name is taken in the world. Change `project` in the command and in `dev.tfvars` so they match.

## 2. Point the app stack at that state

Open `terraform/environments/dev.backend.hcl` and replace the two placeholders with the outputs from step 1. Region stays `us-east-1` unless you chose `us-east-2`. If you use `us-east-2`, change `aws_region` in `dev.tfvars` as well.

```powershell
cd ..
terraform init -backend-config="environments/dev.backend.hcl"
terraform plan -var-file="environments/dev.tfvars"
```

The plan should show the bucket, the jobs table, and the empty API-key secret, plus the pipeline and the agent from the later steps. If you want to create **only storage** first:

```powershell
terraform apply -var-file="environments/dev.tfvars" -target=module.storage
```

**What and why**

- The **S3 bucket** will hold comment JSON and the results file.
- The **DynamoDB table** holds job status and the labeled findings. One table, different keys.
- **Secrets Manager** holds the regulations.gov key. Terraform creates an empty secret. You put the value in yourself so it is never in git.

```powershell
aws secretsmanager put-secret-value --secret-id regs-docket-dev/regs-api-key --secret-string "YOUR_REAL_KEY"
```

**Check:** `aws secretsmanager describe-secret --secret-id regs-docket-dev/regs-api-key` works. Do not print the secret value in a screenshot.

**Common error:** `AccessDeniedException` on the secret means the SSO role cannot write secrets. Ask for `secretsmanager:PutSecretValue` on `regs-docket-dev/*`.

## 3. Pipeline code, before any more AWS

From the repo root:

```powershell
py -m pip install -r requirements.txt pytest
py -m pytest src/backend -q
py scripts/run_local_docket.py
```

The local script pulls a five-comment fake docket, clusters the form letter, and prints themes. No AWS.

**What the two Lambdas will do later**

- `pull-page` asks regulations.gov for one page of comments and saves each comment as JSON in S3. A rate-limit error is named `RateLimited` so Step Functions can wait and retry.
- `analyze` reads those files, runs the same MinHash clustering as the web app (similarity 0.8, at least 3 copies), then asks **Bedrock** to name the theme, stance, and commenter type.

Build the zip Terraform will upload:

```powershell
py scripts/build_pipeline_zips.py
```

## 4. Step Functions and the two Lambdas

**What and why**

- **Lambda** is the short worker. Each page is its own run, so a failure retries that page only.
- **Step Functions** is the conductor. It can run for hours. It fetches a few pages at a time (`map_max_concurrency`, default 2) so the key stays near 1,000 calls an hour.
- **CloudWatch Logs** keeps the error log if a page fails.

```powershell
cd terraform
terraform apply -var-file="environments/dev.tfvars"
```

If you are still going one piece at a time, the pipeline is `module.pipeline`. A full apply here also creates the ECR repository and the AgentCore runtime in step 5, and the runtime apply **fails if the image is not in ECR yet**. Prefer:

```powershell
terraform apply -var-file="environments/dev.tfvars" -target=module.pipeline
```

**Check:** in the AWS console, Step Functions shows `regs-docket-dev-analysis`. Both Lambda functions exist.

**Common error:** `Could not unzip` or `No module named 'requests'` means `build_pipeline_zips.py` was not run, or it was run and then the `build` folder was deleted.

## 5. Agent image, then AgentCore Runtime

**What and why**

- **ECR** stores the container image.
- **AgentCore Runtime** runs that image when you invoke the agent. It must be **linux/arm64**. An ordinary Windows image will not start.
- The agent's tools are Python functions in the image. They start the state machine and read DynamoDB. There is no Gateway.

Create the repository first if the full apply has not already created it:

```powershell
terraform apply -var-file="environments/dev.tfvars" -target=module.agent.aws_ecr_repository.agent
$account = aws sts get-caller-identity --query Account --output text
$region = "us-east-1"
$repo = "$account.dkr.ecr.$region.amazonaws.com/regs-docket-dev-agent"
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin "$account.dkr.ecr.$region.amazonaws.com"
cd ..
docker buildx build --platform linux/arm64 -f src/backend/agent/Dockerfile -t "$repo:latest" --push .
```

Docker Desktop on Windows can cross-build arm64, but that is worth confirming on your machine. If the build says `exec format error` or cannot find a builder, open Docker Desktop, enable the containerd image store, and run `docker buildx create --use` once.

Then create the runtime:

```powershell
cd terraform
terraform apply -var-file="environments/dev.tfvars"
```

**Check:** `terraform output agent_runtime_arn` prints an ARN. `terraform output agent_repository_url` matches the `$repo` you pushed.

**Common error:** `image not found` or `manifest unknown` means the push used the wrong tag or the wrong account. The runtime looks for `:latest` unless you changed `agent_image_tag`.

## 6. Run a docket, then ask the agent

Pick a **small** document for a live test. A large one is the Sunday-night run in the demo script below.

```powershell
$machine = terraform output -raw state_machine_arn
$job = [guid]::NewGuid().ToString("N")
$start = aws stepfunctions start-execution --state-machine-arn $machine --name $job --input "{`"jobId`":`"$job`",`"documentId`":`"FDA-2025-N-3262-0001`"}" --output json | ConvertFrom-Json
aws stepfunctions describe-execution --execution-arn $start.executionArn
```

The job id in `--name` and in the JSON must be the same. Open the execution in the console and watch `Plan`, then `MapPages`, then `Analyze`. Status in DynamoDB moves from `RUNNING` to `SUCCEEDED`.

Ask the agent from the laptop:

```powershell
$env:AGENT_RUNTIME_ARN = terraform output -raw agent_runtime_arn
$env:AWS_REGION = "us-east-1"
$env:PROMPT = "How many form letters were found, and what are the top themes?"
py ..\scripts\invoke_agent.py
$env:PROMPT = "Which arguments come from industry?"
py ..\scripts\invoke_agent.py
```

**Common error:** `AccessDeniedException` on `bedrock:InvokeModel` means the dev account has not turned on the model. The default id is `us.amazon.nova-lite-v1:0`. In the Bedrock console, open Model access and confirm Nova Lite, or change `bedrock_model_id` in `dev.tfvars` to a profile the account already has and apply again. That model id is the one thing to double-check before the demo; it is also marked in `terraform/variables.tf`.

**Common error:** `runtimeSessionId` too short. The script makes a long id. If you set `AGENT_SESSION_ID` yourself, use at least 33 characters.

## What to show on Monday

Do this the day before, not live:

1. Start the state machine on the real document id you want to talk about.
2. Wait until the execution is `SUCCEEDED`. A big docket is supposed to take hours. That is the point.
3. Open the execution graph so you can scroll back through pages and retries.

Live, in this order:

1. Show the graph and one `RateLimited` retry if one happened. Say the key allows about 1,000 calls an hour, so the machine waits instead of failing the whole job.
2. Run `py scripts/run_local_docket.py` only if you want a 10-second backup. It is fake data.
3. Invoke the agent with the two prompts above. The agent should call tools and quote a comment id. If it answers with no tool call, ask again: "Use the findings for document FDA-...".

Do not paste the API key or the Bedrock answer into a ticket.

## Tear it down

From `terraform`, after you have the bucket name from `terraform output comments_bucket`:

```powershell
$bucket = terraform output -raw comments_bucket
aws s3 rm "s3://$bucket" --recursive
terraform destroy -var-file="environments/dev.tfvars"
```

ECR images are deleted with the repository (`force_delete`). The Secrets Manager secret stays in a 7-day recovery window. # VERIFY: that window is the service default and still bills a small amount until it expires. Delete it immediately only if your account policy allows `aws secretsmanager delete-secret --force-delete-without-recovery`.

Destroying bootstrap deletes the state bucket. Do that **after** the app stack is destroyed, and only if you copied anything you still need out of the state bucket. If the state bucket is destroyed first, Terraform forgets how to destroy the rest.

```powershell
cd bootstrap
terraform destroy -var="project=regs-docket" -var="environment=dev" -var="aws_region=us-east-1" -var="github_repository=blahkay15/Reglations.gov"
```

## Checks you can run with no AWS account

```powershell
py -m pytest src/backend -q
terraform fmt -check -recursive
cd terraform
terraform init -backend=false
terraform validate
cd bootstrap
terraform init -backend=false
terraform validate
```
