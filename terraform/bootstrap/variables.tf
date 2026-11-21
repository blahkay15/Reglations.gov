variable "project" {
  type = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must start with a letter and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "environment" {
  type    = string
  default = "dev"

  validation {
    condition     = var.environment == "dev"
    error_message = "Bootstrap for this demo is dev-only."
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"

  validation {
    condition     = contains(["us-east-1", "us-east-2"], var.aws_region)
    error_message = "aws_region must be us-east-1 or us-east-2."
  }
}

variable "github_repository" {
  type        = string
  description = "GitHub repo allowed to assume the deploy role, for example blahkay15/Reglations.gov."
}

variable "tags" {
  type    = map(string)
  default = {}
}
