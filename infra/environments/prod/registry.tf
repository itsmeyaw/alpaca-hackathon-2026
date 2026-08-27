resource "aws_ecr_repository" "trader" {
  name                 = "${local.name_prefix}/trader"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }

  lifecycle {
    prevent_destroy = true
  }
}
