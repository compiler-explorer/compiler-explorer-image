resource "aws_alb" "GccExplorerApp" {
  idle_timeout    = 60
  internal        = false
  name            = "GccExplorerApp"
  security_groups = [
    aws_security_group.CompilerExplorerAlb.id
  ]
  subnets = local.all_subnet_ids

  enable_deletion_protection = false

  access_logs {
    bucket  = aws_s3_bucket.compiler-explorer-logs.bucket
    prefix  = "elb"
    enabled = true
  }
}

resource "aws_alb_listener" "compiler-explorer-alb-listen-http" {
  default_action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.ce["prod"].arn
  }

  load_balancer_arn = aws_alb.GccExplorerApp.arn
  port              = 80
  protocol          = "HTTP"
}

resource "aws_alb_listener_rule" "compiler-explorer-alb-listen-http-beta" {
  priority = 1
  action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.ce["beta"].arn
  }
  condition {
    path_pattern {
      values = ["/beta*"]
    }
  }
  listener_arn = aws_alb_listener.compiler-explorer-alb-listen-http.arn
}

resource "aws_alb_listener_rule" "compiler-explorer-alb-listen-http-staging" {
  priority = 2
  action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.ce["staging"].arn
  }
  condition {
    path_pattern {
      values = [
        "/staging*"
      ]
    }
  }
  listener_arn = aws_alb_listener.compiler-explorer-alb-listen-http.arn
}

resource "aws_alb_listener" "compiler-explorer-alb-listen-https" {
  default_action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.ce["prod"].arn
  }
  load_balancer_arn = aws_alb.GccExplorerApp.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-2015-05"
  certificate_arn   = data.aws_acm_certificate.godbolt-org-et-al.arn
}

resource "aws_alb_listener_rule" "compiler-explorer-alb-listen-https-beta" {
  priority = 1
  action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.ce["beta"].arn
  }
  condition {
    path_pattern {
      values = [
        "/beta*"
      ]
    }
  }
  listener_arn = aws_alb_listener.compiler-explorer-alb-listen-https.arn
}

resource "aws_alb_listener_rule" "compiler-explorer-alb-listen-https-staging" {
  priority = 2
  action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.ce["staging"].arn
  }
  condition {
    path_pattern {
      values = [
        "/staging*"
      ]
    }
  }
  listener_arn = aws_alb_listener.compiler-explorer-alb-listen-https.arn
}


resource "aws_alb_listener" "ceconan-alb-listen-http" {
  default_action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.conan.arn
  }

  load_balancer_arn = aws_alb.GccExplorerApp.arn
  port              = 1080
  protocol          = "HTTP"
}

resource "aws_alb_listener" "ceconan-alb-listen-https" {
  default_action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.conan.arn
  }
  load_balancer_arn = aws_alb.GccExplorerApp.arn
  port              = 1443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-2015-05"
  certificate_arn   = data.aws_acm_certificate.godbolt-org-et-al.arn
}

resource "aws_alb_target_group" "lambda" {
  name        = "AwsLambdaTargetGroup"
  target_type = "lambda"
}

resource "aws_alb_target_group_attachment" "lambda-stats-endpoint" {
  target_group_arn = aws_alb_target_group.lambda.arn
  target_id        = aws_lambda_function.stats.arn
  depends_on       = [aws_lambda_permission.from_alb]
}

resource "aws_alb_listener_rule" "compiler-explorer-alb-listen-https-lambda" {
  priority = 3
  action {
    type             = "forward"
    target_group_arn = aws_alb_target_group.lambda.arn
  }
  condition {
    host_header {
      values = ["lambda.compiler-explorer.com"]
    }
  }
  listener_arn = aws_alb_listener.compiler-explorer-alb-listen-https.arn
}

resource "aws_alb_listener_rule" "compiler-explorer-alb-listen-https-stats" {
  priority = 5
  action {
    type = "redirect"
    redirect {
      status_code = "HTTP_301"
      host        = "ce.grafana.net"
      path        = "/public-dashboards/326d9aa2606b4efea25f4458a4c3f065"
      query       = "orgId=0&refresh=1m"
    }
  }
  condition {
    host_header {
      values = ["stats.compiler-explorer.com"]
    }
  }
  listener_arn = aws_alb_listener.compiler-explorer-alb-listen-https.arn
}
