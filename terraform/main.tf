locals {
  name_prefix = "${var.project}-${var.environment}"
}

module "storage" {
  source = "./modules/storage"

  name_prefix = local.name_prefix
  tags        = var.tags
}

module "pipeline" {
  source = "./modules/pipeline"

  name_prefix          = local.name_prefix
  aws_region           = var.aws_region
  bucket_name          = module.storage.bucket_name
  bucket_arn           = module.storage.bucket_arn
  table_name           = module.storage.table_name
  table_arn            = module.storage.table_arn
  api_key_secret_arn   = module.storage.api_key_secret_arn
  page_size            = var.page_size
  map_max_concurrency  = var.map_max_concurrency
  bedrock_model_id     = var.bedrock_model_id
  max_labeled_clusters = var.max_labeled_clusters
  tags                 = var.tags
}
