locals {
  name_prefix = "${var.project}-${var.environment}"
}

module "storage" {
  source = "./modules/storage"

  name_prefix = local.name_prefix
  tags        = var.tags
}
