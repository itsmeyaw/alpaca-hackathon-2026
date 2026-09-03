variable "aws_account_id" {
  type        = string
  description = "AWS account that may receive Catalyst Router resources."
  default     = "109850456914"

  validation {
    condition     = var.aws_account_id == "109850456914"
    error_message = "This configuration may only target AWS account 109850456914."
  }
}

variable "aws_region" {
  type        = string
  description = "AWS region for Catalyst Router."
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "Catalyst Router is fixed to us-east-1."
  }
}

variable "environment" {
  type        = string
  description = "Deployment environment name."
  default     = "prod"

  validation {
    condition     = var.environment == "prod"
    error_message = "Only the prod competition environment is currently supported."
  }
}

variable "runtime_image_tag" {
  type        = string
  description = "Immutable ECR image tag deployed to both App Runner and the ECS worker."

  validation {
    condition     = length(var.runtime_image_tag) > 0 && var.runtime_image_tag != "latest"
    error_message = "runtime_image_tag must be a non-latest immutable tag."
  }
}

variable "worker_alarm_actions" {
  type        = list(string)
  description = "SNS action ARNs notified when the worker is not running."
  default     = []
}

variable "challenger_run_id" {
  type        = string
  description = "Immutable shadow challenger artifact directory in the archive bucket."
}

variable "challenger_manifest_sha256" {
  type        = string
  description = "Trusted SHA-256 digest for the deployed challenger manifest."

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.challenger_manifest_sha256))
    error_message = "challenger_manifest_sha256 must be a lowercase SHA-256 digest."
  }
}

variable "bedrock_model_id" {
  type        = string
  description = "Bedrock model or inference-profile ID used for shadow Event extraction."

  validation {
    condition     = length(var.bedrock_model_id) > 0
    error_message = "bedrock_model_id must not be empty."
  }
}

variable "bedrock_model_arn" {
  type        = string
  description = "Exact Bedrock foundation-model or inference-profile ARN allowed to receive Events."

  validation {
    condition     = startswith(var.bedrock_model_arn, "arn:aws:bedrock:")
    error_message = "bedrock_model_arn must be a Bedrock ARN."
  }
}

variable "model_paper_execution_enabled" {
  type        = bool
  description = "Explicitly authorize the selected ADR-0013 15-minute model for paper execution."
  default     = false
}

variable "model_options_execution_enabled" {
  type        = bool
  description = "Replace directional equity entries with bounded long calls and puts."
  default     = false
}
