variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "state_machine_arn" {
  type = string
}

variable "bedrock_model_id" {
  type = string
}

variable "agent_image_tag" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
