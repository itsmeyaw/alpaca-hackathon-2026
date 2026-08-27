resource "aws_cloudwatch_log_group" "trader" {
  name              = "/ecs/${local.name_prefix}/trader"
  retention_in_days = 30
  log_group_class   = "STANDARD"
  skip_destroy      = true
}
