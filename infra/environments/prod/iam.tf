data "aws_iam_policy_document" "ecs_tasks_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:*"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name_prefix}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json
}

data "aws_iam_policy_document" "task_execution" {
  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullTraderImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = [aws_ecr_repository.trader.arn]
  }

  statement {
    sid    = "WriteTraderLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.trader.arn}:*"]
  }

  statement {
    sid       = "ReadRuntimeSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.runtime.arn]
  }
}

resource "aws_iam_role_policy" "task_execution" {
  name   = "${local.name_prefix}-task-execution"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution.json
}

resource "aws_iam_role" "trader" {
  name               = "${local.name_prefix}-trader-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json
}

data "aws_iam_policy_document" "trader" {
  statement {
    sid    = "OperationalState"
    effect = "Allow"
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:ConditionCheckItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
    ]
    resources = [aws_dynamodb_table.operational.arn]
  }

  statement {
    sid       = "ReadChallengerArtifacts"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.archive.arn}/models/${var.challenger_run_id}/*"]
  }
}

resource "aws_iam_role_policy" "trader" {
  name   = "${local.name_prefix}-trader"
  role   = aws_iam_role.trader.id
  policy = data.aws_iam_policy_document.trader.json
}
