output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.locks.name
}

output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}
