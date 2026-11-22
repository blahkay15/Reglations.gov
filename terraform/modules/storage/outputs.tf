output "bucket_name" {
  value = aws_s3_bucket.comments.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.comments.arn
}

output "table_name" {
  value = aws_dynamodb_table.jobs.name
}

output "table_arn" {
  value = aws_dynamodb_table.jobs.arn
}

output "api_key_secret_arn" {
  value = aws_secretsmanager_secret.regs_api_key.arn
}

output "api_key_secret_name" {
  value = aws_secretsmanager_secret.regs_api_key.name
}
