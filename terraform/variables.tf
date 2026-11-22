variable "project" {
  type        = string
  description = "Short project prefix used in resource names. Lowercase letters, numbers, and hyphens."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must start with a letter and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "environment" {
  type        = string
  description = "Environment name. This demo stack refuses anything other than dev."
  default     = "dev"

  validation {
    condition     = var.environment == "dev"
    error_message = "This AgentCore demo is dev-only. environment must be \"dev\"."
  }
}

variable "aws_region" {
  type        = string
  description = "AWS region for this demo. AgentCore Runtime is used in us-east-1 or us-east-2."
  default     = "us-east-1"

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.aws_region)
    error_message = "aws_region must be us-east-1 or us-east-2."
  }
}

variable "tags" {
  type        = map(string)
  description = "Optional tags. This stack does not add its own tagging scheme."
  default     = {}
}

variable "page_size" {
  type        = number
  description = "Comments requested per regulations.gov list page."
  default     = 20

  validation {
    condition     = var.page_size >= 5 && var.page_size <= 250
    error_message = "page_size must be between 5 and 250."
  }
}

variable "map_max_concurrency" {
  type        = number
  description = "How many comment pages Step Functions fetches at once."
  default     = 2

  validation {
    condition     = var.map_max_concurrency >= 1 && var.map_max_concurrency <= 5
    error_message = "map_max_concurrency must be from 1 to 5 so the API key stays near 1,000 calls per hour."
  }
}

variable "bedrock_model_id" {
  type        = string
  description = "Bedrock model or inference profile id used to label comment themes."
  # VERIFY: confirm this inference profile is enabled in the target account and region.
  default = "us.amazon.nova-lite-v1:0"
}

variable "max_labeled_clusters" {
  type        = number
  description = "Upper bound on how many clusters the analyze step sends to Bedrock."
  default     = 40

  validation {
    condition     = var.max_labeled_clusters >= 1 && var.max_labeled_clusters <= 200
    error_message = "max_labeled_clusters must be between 1 and 200."
  }
}

variable "agent_image_tag" {
  type        = string
  description = "Tag of the ARM64 agent image pushed to this stack's ECR repository."
  default     = "latest"
}
