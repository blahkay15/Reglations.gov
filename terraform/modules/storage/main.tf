data "aws_caller_identity" "current" {}

# One bucket for raw comment JSON and the analysis results. The name includes
# the account id from a data source so two dev accounts do not collide.
resource "aws_s3_bucket" "comments" {
  bucket = "${var.name_prefix}-docket-${data.aws_caller_identity.current.account_id}"
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "comments" {
  bucket = aws_s3_bucket.comments.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "comments" {
  bucket = aws_s3_bucket.comments.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Single table. Job status is JOB#<id> / META. Findings are JOB#<id> / FINDING#<n>.
# The latest job for a document is DOC#<documentId> / LATEST.
resource "aws_dynamodb_table" "jobs" {
  name         = "${var.name_prefix}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = var.tags
}

# Shell only. Blake sets the value with put-secret-value. Terraform never sees the key.
resource "aws_secretsmanager_secret" "regs_api_key" {
  name        = "${var.name_prefix}/regs-api-key"
  description = "regulations.gov API key for the dev AgentCore docket demo. Set the value manually."
  tags        = var.tags
}
