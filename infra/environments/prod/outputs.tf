output "operational_table_name" { value = aws_dynamodb_table.operational.name }
output "archive_bucket_name" { value = aws_s3_bucket.archive.id }
output "dashboard_bucket_name" { value = aws_s3_bucket.dashboard.id }
output "ecr_repository_url" { value = aws_ecr_repository.trader.repository_url }
output "runtime_secret_name" { value = aws_secretsmanager_secret.runtime.name }
output "runtime_secret_arn" { value = aws_secretsmanager_secret.runtime.arn }
output "trader_log_group_name" { value = aws_cloudwatch_log_group.trader.name }
output "ecs_cluster_name" { value = aws_ecs_cluster.trader.name }
output "task_execution_role_arn" { value = aws_iam_role.task_execution.arn }
output "trader_task_role_arn" { value = aws_iam_role.trader.arn }

output "trader_environment" {
  value = {
    AWS_REGION           = var.aws_region
    STATE_BACKEND        = "dynamodb"
    DYNAMODB_TABLE       = aws_dynamodb_table.operational.name
    COMPETITION_ID       = "alpaca-hackathon-2026"
    AUTO_RECONCILE       = "true"
    PUBLIC_DELAY_SECONDS = "900"
  }
}
