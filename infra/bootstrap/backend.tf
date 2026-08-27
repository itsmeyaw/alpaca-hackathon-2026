terraform {
  backend "s3" {
    bucket              = "catalyst-router-tfstate-109850456914-us-east-1"
    key                 = "bootstrap/terraform.tfstate"
    region              = "us-east-1"
    encrypt             = true
    use_lockfile        = true
    allowed_account_ids = ["109850456914"]
  }
}
