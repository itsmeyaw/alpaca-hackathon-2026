resource "aws_ecs_cluster" "trader" {
  name = local.name_prefix
}
