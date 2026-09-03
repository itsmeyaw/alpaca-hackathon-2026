data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "map-public-ip-on-launch"
    values = ["true"]
  }
}

data "aws_ecr_image" "runtime" {
  repository_name = aws_ecr_repository.trader.name
  image_tag       = var.runtime_image_tag
}

resource "aws_security_group" "trader" {
  name        = "${local.name_prefix}-trader"
  description = "No ingress; HTTPS egress for AWS and Alpaca"
  vpc_id      = data.aws_vpc.default.id

  egress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_cluster" "trader" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "trader" {
  family                   = "${local.name_prefix}-trader"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.trader.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name                   = "trader"
      image                  = "${aws_ecr_repository.trader.repository_url}@${data.aws_ecr_image.runtime.image_digest}"
      essential              = true
      command                = ["catalyst-router", "worker"]
      user                   = "10001:10001"
      readonlyRootFilesystem = false
      stopTimeout            = 120

      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "STATE_BACKEND", value = "dynamodb" },
        { name = "DYNAMODB_TABLE", value = aws_dynamodb_table.operational.name },
        { name = "COMPETITION_ID", value = "alpaca-hackathon-2026" },
        { name = "RUNTIME_ROLE", value = "worker" },
        { name = "AUTO_RECONCILE", value = "false" },
        { name = "PUBLIC_DELAY_SECONDS", value = "900" },
        { name = "WORKER_POLL_SECONDS", value = "15" },
        { name = "PAPER_EXECUTION_ENABLED", value = "true" },
        { name = "MODEL_EXECUTION_ENABLED", value = tostring(var.model_paper_execution_enabled) },
        { name = "MODEL_OPTIONS_EXECUTION_ENABLED", value = tostring(var.model_options_execution_enabled) },
        { name = "MODEL_AUTHORITY", value = var.model_paper_execution_enabled ? "PAPER_LIVE" : "SHADOW_ONLY" },
        { name = "MODEL_DECISION_GATE", value = "0.52" },
        { name = "LLM_EVENTS_ENABLED", value = "true" },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "BEDROCK_PROMPT_VERSION", value = "event-v1" },
        {
          name  = "CHALLENGER_MANIFEST_URI"
          value = "s3://${aws_s3_bucket.archive.id}/models/${var.challenger_run_id}/manifest.json"
        },
        {
          name  = "CHALLENGER_MANIFEST_SHA256"
          value = var.challenger_manifest_sha256
        }
      ]

      secrets = [
        {
          name      = "ALPACA_CREDENTIALS"
          valueFrom = aws_secretsmanager_secret.runtime.arn
        }
      ]

      linuxParameters = {
        initProcessEnabled = true
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.trader.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }

      healthCheck = {
        command = [
          "CMD-SHELL",
          "test -f /tmp/worker-heartbeat && test $(( $(date +%s) - $(stat -c %Y /tmp/worker-heartbeat) )) -lt 300"
        ]
        interval    = 60
        timeout     = 5
        retries     = 3
        startPeriod = 180
      }
    }
  ])

  depends_on = [aws_iam_role_policy.task_execution, aws_iam_role_policy.trader]

  lifecycle {
    precondition {
      condition     = !var.model_options_execution_enabled || var.model_paper_execution_enabled
      error_message = "model_options_execution_enabled requires model_paper_execution_enabled."
    }
  }
}

resource "aws_ecs_service" "trader" {
  name            = "${local.name_prefix}-trader"
  cluster         = aws_ecs_cluster.trader.id
  task_definition = aws_ecs_task_definition.trader.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  platform_version                   = "LATEST"
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  availability_zone_rebalancing      = "DISABLED"
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"

  # New readers retain compatibility with the previous state schema. Deploy
  # reporting before allowing a worker with a new schema to write durable state.
  depends_on = [aws_apprunner_service.api]

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.trader.id]
    assign_public_ip = true
  }
}
