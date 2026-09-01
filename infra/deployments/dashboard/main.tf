terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.33"
    }
  }

  backend "s3" {
    bucket              = "catalyst-router-tfstate-109850456914-us-east-1"
    key                 = "deployments/dashboard/terraform.tfstate"
    region              = "us-east-1"
    encrypt             = true
    use_lockfile        = true
    allowed_account_ids = ["109850456914"]
  }
}

provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["109850456914"]

  default_tags {
    tags = {
      Project     = "catalyst-router"
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}

data "terraform_remote_state" "prod" {
  backend = "s3"

  config = {
    bucket  = "catalyst-router-tfstate-109850456914-us-east-1"
    key     = "environments/prod/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
  }
}

locals {
  dashboard_output_dir = "${path.root}/../../../dashboard/out"
  dashboard_files      = fileset(local.dashboard_output_dir, "**")
  dashboard_hash = sha256(join("", [
    for file in sort(tolist(local.dashboard_files)) : "${file}:${filemd5("${local.dashboard_output_dir}/${file}")}"
  ]))
  content_types = {
    css   = "text/css"
    html  = "text/html; charset=utf-8"
    ico   = "image/x-icon"
    jpeg  = "image/jpeg"
    jpg   = "image/jpeg"
    js    = "application/javascript"
    json  = "application/json"
    png   = "image/png"
    svg   = "image/svg+xml"
    txt   = "text/plain; charset=utf-8"
    webp  = "image/webp"
    woff  = "font/woff"
    woff2 = "font/woff2"
  }
}

resource "terraform_data" "dashboard_build" {
  input = local.dashboard_hash

  lifecycle {
    precondition {
      condition     = length(local.dashboard_files) > 0
      error_message = "Build the dashboard before applying its Terraform deployment."
    }
  }
}

resource "aws_s3_object" "dashboard" {
  for_each = local.dashboard_files

  bucket        = data.terraform_remote_state.prod.outputs.dashboard_bucket_name
  key           = each.value
  source        = "${local.dashboard_output_dir}/${each.value}"
  source_hash   = filemd5("${local.dashboard_output_dir}/${each.value}")
  content_type  = lookup(local.content_types, lower(element(reverse(split(".", each.value)), 0)), "application/octet-stream")
  cache_control = startswith(each.value, "_next/static/") ? "public, max-age=31536000, immutable" : "no-cache"

  depends_on = [terraform_data.dashboard_build]
}

resource "terraform_data" "dashboard_cache_invalidation" {
  triggers_replace = [local.dashboard_hash]

  provisioner "local-exec" {
    command = <<-EOT
      invalidation_id="$(aws cloudfront create-invalidation --distribution-id ${data.terraform_remote_state.prod.outputs.cloudfront_distribution_id} --paths '/*' --query 'Invalidation.Id' --output text)"
      aws cloudfront wait invalidation-completed --distribution-id ${data.terraform_remote_state.prod.outputs.cloudfront_distribution_id} --id "$invalidation_id"
    EOT
  }

  depends_on = [aws_s3_object.dashboard]
}
