data "aws_caller_identity" "current" {}

data "archive_file" "pipeline" {
  type        = "zip"
  source_dir  = "${path.module}/../../../build/lambda/pipeline"
  output_path = "${path.module}/.build/pipeline.zip"
}

resource "aws_cloudwatch_log_group" "pull" {
  name              = "/aws/lambda/${var.name_prefix}-pull-page"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "analyze" {
  name              = "/aws/lambda/${var.name_prefix}-analyze"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "states" {
  name              = "/aws/vendedlogs/${var.name_prefix}-analysis"
  retention_in_days = 14
  tags              = var.tags
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pull" {
  name               = "${var.name_prefix}-pull-page"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

resource "aws_iam_role" "analyze" {
  name               = "${var.name_prefix}-analyze"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "pull" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.pull.arn}:*"]
  }

  statement {
    sid       = "ReadWriteComments"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [var.bucket_arn, "${var.bucket_arn}/jobs/*"]
  }

  statement {
    sid       = "JobStatus"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [var.table_arn]
  }

  statement {
    sid       = "ReadApiKey"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.api_key_secret_arn]
  }
}

data "aws_iam_policy_document" "analyze" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.analyze.arn}:*"]
  }

  statement {
    sid       = "ReadCommentsWriteResults"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [var.bucket_arn, "${var.bucket_arn}/jobs/*"]
  }

  statement {
    sid       = "Findings"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"]
    resources = [var.table_arn]
  }

  statement {
    sid = "Bedrock"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    # VERIFY: narrow to the chosen inference profile after model access is confirmed.
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/*",
      "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/*",
    ]
  }
}

resource "aws_iam_role_policy" "pull" {
  name   = "${var.name_prefix}-pull-page"
  role   = aws_iam_role.pull.id
  policy = data.aws_iam_policy_document.pull.json
}

resource "aws_iam_role_policy" "analyze" {
  name   = "${var.name_prefix}-analyze"
  role   = aws_iam_role.analyze.id
  policy = data.aws_iam_policy_document.analyze.json
}

resource "aws_lambda_function" "pull" {
  function_name    = "${var.name_prefix}-pull-page"
  role             = aws_iam_role.pull.arn
  runtime          = "python3.12"
  handler          = "pull_page.handler"
  filename         = data.archive_file.pipeline.output_path
  source_code_hash = data.archive_file.pipeline.output_base64sha256
  timeout          = 900
  memory_size      = 1024
  tags             = var.tags

  environment {
    variables = {
      BUCKET_NAME        = var.bucket_name
      TABLE_NAME         = var.table_name
      API_KEY_SECRET_ARN = var.api_key_secret_arn
      PAGE_SIZE          = tostring(var.page_size)
    }
  }

  depends_on = [aws_cloudwatch_log_group.pull, aws_iam_role_policy.pull]
}

resource "aws_lambda_function" "analyze" {
  function_name    = "${var.name_prefix}-analyze"
  role             = aws_iam_role.analyze.arn
  runtime          = "python3.12"
  handler          = "analyze.handler"
  filename         = data.archive_file.pipeline.output_path
  source_code_hash = data.archive_file.pipeline.output_base64sha256
  timeout          = 900
  memory_size      = 1024
  tags             = var.tags

  environment {
    variables = {
      BUCKET_NAME          = var.bucket_name
      TABLE_NAME           = var.table_name
      BEDROCK_MODEL_ID     = var.bedrock_model_id
      MAX_LABELED_CLUSTERS = tostring(var.max_labeled_clusters)
    }
  }

  depends_on = [aws_cloudwatch_log_group.analyze, aws_iam_role_policy.analyze]
}

data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "states" {
  name               = "${var.name_prefix}-analysis"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "states" {
  statement {
    sid       = "InvokePipeline"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.pull.arn, aws_lambda_function.analyze.arn]
  }

  statement {
    sid = "StateMachineLogs"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "states" {
  name   = "${var.name_prefix}-analysis"
  role   = aws_iam_role.states.id
  policy = data.aws_iam_policy_document.states.json
}

resource "aws_sfn_state_machine" "analysis" {
  name     = "${var.name_prefix}-analysis"
  role_arn = aws_iam_role.states.arn
  type     = "STANDARD"
  tags     = var.tags

  definition = templatefile("${path.module}/state_machine.asl.json.tftpl", {
    pull_arn             = aws_lambda_function.pull.arn
    analyze_arn          = aws_lambda_function.analyze.arn
    page_size            = var.page_size
    map_max_concurrency  = var.map_max_concurrency
    max_labeled_clusters = var.max_labeled_clusters
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.states.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.states]
}
