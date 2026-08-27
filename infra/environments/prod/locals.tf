locals {
  name_prefix = "catalyst-router-${var.environment}"
  bucket_base = "${local.name_prefix}-${var.aws_account_id}-${var.aws_region}"
}
