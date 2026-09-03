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
output "api_service_url" { value = "https://${aws_apprunner_service.api.service_url}" }
output "dashboard_url" { value = "https://${aws_cloudfront_distribution.dashboard.domain_name}" }
output "cloudfront_distribution_id" { value = aws_cloudfront_distribution.dashboard.id }
output "trader_service_name" { value = aws_ecs_service.trader.name }
output "trader_task_definition_arn" { value = aws_ecs_task_definition.trader.arn }
output "trader_security_group_id" { value = aws_security_group.trader.id }

output "trader_environment" {
  value = {
    AWS_REGION           = var.aws_region
    STATE_BACKEND        = "dynamodb"
    DYNAMODB_TABLE       = aws_dynamodb_table.operational.name
    COMPETITION_ID       = "alpaca-hackathon-2026"
    RUNTIME_ROLE         = "worker"
    AUTO_RECONCILE       = "false"
    PUBLIC_DELAY_SECONDS = "900"
    WORKER_POLL_SECONDS  = "15"
    MODEL_EXECUTION      = var.model_paper_execution_enabled ? "PAPER_LIVE" : "SHADOW_ONLY"
    MODEL_OPTIONS        = var.model_options_execution_enabled ? "LONG_CALL_PUT" : "DISABLED"
    MODEL_DECISION_GATE  = "0.52"
    LLM_EVENTS_ENABLED   = "true"
    BEDROCK_MODEL_ID     = var.bedrock_model_id
    BEDROCK_PROMPT       = "event-v1"
  }
}
