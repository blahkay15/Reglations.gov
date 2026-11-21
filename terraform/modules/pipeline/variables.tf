variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "api_key_secret_arn" {
  type = string
}

variable "page_size" {
  type = number
}

variable "map_max_concurrency" {
  type = number
}

variable "bedrock_model_id" {
  type = string
}

variable "max_labeled_clusters" {
  type = number
}

variable "tags" {
  type    = map(string)
  default = {}
}
