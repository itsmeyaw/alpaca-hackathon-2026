resource "aws_secretsmanager_secret" "runtime" {
  name                    = "catalyst-router/${var.environment}/runtime"
  description             = "Alpaca paper-trading credentials for Catalyst Router"
  recovery_window_in_days = 30

  lifecycle {
    prevent_destroy = true
  }
}
