output "comments_bucket" {
  description = "S3 bucket for raw comments and analysis results."
  value       = module.storage.bucket_name
}

output "jobs_table" {
  description = "DynamoDB table for job status and findings."
  value       = module.storage.table_name
}

output "api_key_secret_arn" {
  description = "Secrets Manager ARN. Put the regulations.gov key in with the AWS CLI. The value is not in Terraform."
  value       = module.storage.api_key_secret_arn
}

output "aws_region" {
  description = "Region this stack was applied in."
  value       = data.aws_region.current.region
}

output "account_id" {
  description = "Account this stack was applied in."
  value       = data.aws_caller_identity.current.account_id
}

output "state_machine_arn" {
  description = "Step Functions state machine that pulls pages and then analyzes them."
  value       = module.pipeline.state_machine_arn
}

output "agent_repository_url" {
  description = "ECR repository for the ARM64 agent image. Push :latest before the runtime can start."
  value       = module.agent.repository_url
}

output "agent_runtime_arn" {
  description = "Pass this to scripts/invoke_agent.py as AGENT_RUNTIME_ARN."
  value       = module.agent.agent_runtime_arn
}
