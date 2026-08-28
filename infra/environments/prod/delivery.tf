data "aws_iam_policy_document" "app_runner_access_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app_runner_access" {
  name               = "${local.name_prefix}-app-runner-access"
  assume_role_policy = data.aws_iam_policy_document.app_runner_access_assume_role.json
}

data "aws_iam_policy_document" "app_runner_access" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.trader.arn]
  }
}

resource "aws_iam_role_policy" "app_runner_access" {
  name   = "${local.name_prefix}-app-runner-access"
  role   = aws_iam_role.app_runner_access.id
  policy = data.aws_iam_policy_document.app_runner_access.json
}

data "aws_iam_policy_document" "app_runner_instance_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app_runner_instance" {
  name               = "${local.name_prefix}-app-runner-instance"
  assume_role_policy = data.aws_iam_policy_document.app_runner_instance_assume_role.json
}

data "aws_iam_policy_document" "app_runner_instance" {
  statement {
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.operational.arn]
  }

  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.archive.arn}/models/${var.challenger_run_id}/*"]
  }

}

data "aws_s3_object" "challenger_manifest" {
  bucket = aws_s3_bucket.archive.id
  key    = "models/${var.challenger_run_id}/manifest.json"
}

data "aws_s3_object" "challenger_model" {
  bucket = aws_s3_bucket.archive.id
  key    = "models/${var.challenger_run_id}/model.ubj"
}

resource "aws_iam_role_policy" "app_runner_instance" {
  name   = "${local.name_prefix}-app-runner-instance"
  role   = aws_iam_role.app_runner_instance.id
  policy = data.aws_iam_policy_document.app_runner_instance.json
}

resource "aws_apprunner_service" "api" {
  service_name = "${local.name_prefix}-api"
  depends_on   = [aws_iam_role_policy.app_runner_instance]

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.app_runner_access.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.trader.repository_url}:${var.api_image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          AWS_REGION                 = var.aws_region
          STATE_BACKEND              = "dynamodb"
          DYNAMODB_TABLE             = aws_dynamodb_table.operational.name
          COMPETITION_ID             = "alpaca-hackathon-2026"
          RUNTIME_ROLE               = "reporting"
          AUTO_RECONCILE             = "false"
          PUBLIC_DELAY_SECONDS       = "900"
          CHALLENGER_MANIFEST_URI    = "s3://${aws_s3_bucket.archive.id}/models/${var.challenger_run_id}/manifest.json"
          CHALLENGER_MANIFEST_SHA256 = var.challenger_manifest_sha256
        }
        runtime_environment_secrets = {}
      }
    }
  }

  instance_configuration {
    cpu               = "1 vCPU"
    memory            = "2 GB"
    instance_role_arn = aws_iam_role.app_runner_instance.arn
  }

  health_check_configuration {
    healthy_threshold   = 1
    interval            = 10
    path                = "/health"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 5
  }

  lifecycle {
    precondition {
      condition     = data.aws_s3_object.challenger_manifest.content_length > 0 && data.aws_s3_object.challenger_model.content_length > 0
      error_message = "The selected challenger manifest and model must exist before deployment."
    }
  }
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  name                              = "${local.name_prefix}-dashboard"
  description                       = "Private dashboard bucket access"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "dashboard" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "Catalyst Router public dashboard"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    domain_name                 = aws_s3_bucket.dashboard.bucket_regional_domain_name
    origin_id                   = "dashboard-s3"
    origin_access_control_id    = aws_cloudfront_origin_access_control.dashboard.id
    origin_path                 = ""
    response_completion_timeout = 0

  }

  origin {
    domain_name                 = aws_apprunner_service.api.service_url
    origin_id                   = "public-api"
    origin_path                 = ""
    response_completion_timeout = 0

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "dashboard-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "public-api"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1"
  }
}
