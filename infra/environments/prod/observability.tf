resource "aws_cloudwatch_log_group" "trader" {
  name              = "/ecs/${local.name_prefix}/trader"
  retention_in_days = 30
  log_group_class   = "STANDARD"
  skip_destroy      = true
}

resource "aws_cloudwatch_metric_alarm" "trader_not_running" {
  alarm_name          = "${local.name_prefix}-trader-not-running"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = var.worker_alarm_actions

  dimensions = {
    ClusterName = aws_ecs_cluster.trader.name
    ServiceName = aws_ecs_service.trader.name
  }
}
