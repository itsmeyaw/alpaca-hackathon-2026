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
