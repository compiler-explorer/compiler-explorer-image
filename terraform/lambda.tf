data "aws_iam_policy_document" "aws_lambda_trust_policy" {
  statement {
    actions = ["sts:AssumeRole"]
    effect  = "Allow"
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "iam_for_lambda" {
  name               = "iam_for_lambda"
  assume_role_policy = data.aws_iam_policy_document.aws_lambda_trust_policy.json
}

resource "aws_iam_role_policy_attachment" "terraform_lambda_policy" {
  role       = aws_iam_role.iam_for_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "aws_lambda_stats" {
  statement {
    sid       = "WriteStatsLog"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.compiler-explorer-logs.arn}/stats/*"]
  }
  statement {
    sid     = "AccessSQS"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:SendMessage"
    ]
    resources = [aws_sqs_queue.stats_queue.arn]
  }
  statement {
    sid     = "ReadCompilerDb"
    actions = [
      "dynamodb:Query"
    ]
    resources = [aws_dynamodb_table.compiler-builds.arn]
  }
}

resource "aws_iam_policy" "aws_lambda_stats" {
  name        = "aws_lambda_stats_logs"
  description = "Lambda stats policy (SNS, dynamodb)"
  policy      = data.aws_iam_policy_document.aws_lambda_stats.json
}

resource "aws_iam_role_policy_attachment" "aws_lambda_stats" {
  role       = aws_iam_role.iam_for_lambda.name
  policy_arn = aws_iam_policy.aws_lambda_stats.arn
}

data "aws_ssm_parameter" "discord_webhook_url" {
  name = "/admin/discord_webhook_url"
}

resource "aws_sns_topic_subscription" "sns_to_lambda" {
  topic_arn = data.aws_sns_topic.alert.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.cloudwatch_to_discord.arn
}

data "aws_s3_object" "lambda_zip" {
  # Lambda zip is uploaded and rebuild by the Makefile: make upload-lambda
  bucket = aws_s3_bucket.compiler-explorer.bucket
  key    = "lambdas/lambda-package.zip"
}

data "aws_s3_object" "lambda_zip_sha" {
  # Lambda zip's SHA256 is uploaded and rebuild by the Makefile: make upload-lambda
  bucket = aws_s3_bucket.compiler-explorer.bucket
  key    = "lambdas/lambda-package.zip.sha256"
}

resource "aws_lambda_function" "cloudwatch_to_discord" {
  description       = "Dispatch cloudwatch messages to discord"
  s3_bucket         = data.aws_s3_object.lambda_zip.bucket
  s3_key            = data.aws_s3_object.lambda_zip.key
  s3_object_version = data.aws_s3_object.lambda_zip.version_id
  source_code_hash  = chomp(data.aws_s3_object.lambda_zip_sha.body)
  function_name     = "cloudwatch_to_discord"
  role              = aws_iam_role.iam_for_lambda.arn
  handler           = "cloudwatch_to_discord.lambda_handler"

  runtime = "python3.8"

  environment {
    variables = {
      WEBHOOK_URL = data.aws_ssm_parameter.discord_webhook_url.value
    }
  }
}


resource "aws_lambda_permission" "with_sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cloudwatch_to_discord.arn
  principal     = "sns.amazonaws.com"
  source_arn    = data.aws_sns_topic.alert.arn
}

resource "aws_lambda_function" "stats" {
  description       = "Respond to various CE-specific stats records"
  s3_bucket         = aws_s3_bucket.compiler-explorer.bucket
  s3_key            = data.aws_s3_object.lambda_zip.key
  s3_object_version = data.aws_s3_object.lambda_zip.version_id
  source_code_hash  = chomp(data.aws_s3_object.lambda_zip_sha.body)
  function_name     = "stats"
  role              = aws_iam_role.iam_for_lambda.arn
  handler           = "stats.lambda_handler"
  timeout           = 10

  runtime = "python3.8"

  environment {
    variables = {
      S3_BUCKET_NAME       = aws_s3_bucket.compiler-explorer-logs.bucket
      SQS_STATS_QUEUE      = aws_sqs_queue.stats_queue.id
      COMPILER_BUILD_TABLE = aws_dynamodb_table.compiler-builds.name
    }
  }
}

resource "aws_sqs_queue" "stats_queue" {
  name                      = "CompilerExplorerStats"
  max_message_size          = 1024
  message_retention_seconds = 600
}

resource "aws_lambda_permission" "from_alb" {
  statement_id  = "AllowExecutionFromALB"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stats.arn
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_alb_target_group.lambda.arn
}

resource "aws_lambda_permission" "from_sqs" {
  statement_id  = "AllowExecutionFromSQS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stats.arn
  principal     = "sqs.amazonaws.com"
  source_arn    = aws_sqs_queue.stats_queue.arn
}

resource "aws_lambda_event_source_mapping" "sqs_to_lambda" {
  event_source_arn                   = aws_sqs_queue.stats_queue.arn
  function_name                      = aws_lambda_function.stats.arn
  batch_size                         = 100
  maximum_batching_window_in_seconds = 300
}
