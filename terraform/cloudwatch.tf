data "aws_sns_topic" "alert" {
  name = "CompilerExplorerAlert"
}

locals {
  approx_monthly_budget = 750
  alert_every           = 250
}

resource "aws_cloudwatch_metric_alarm" "resp_90ile_15m_too_slow" {
  alarm_name          = "SiteTooSlow"
  alarm_description   = "Monitor site response time, 90%ile being too slow"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 4
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  extended_statistic  = "p90"
  period              = 15 * 60

  dimensions = {
    LoadBalancer = aws_alb.GccExplorerApp.arn_suffix
  }
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [data.aws_sns_topic.alert.arn]
}

resource "aws_cloudwatch_metric_alarm" "spending_alert" {
  count               = local.approx_monthly_budget * 2 / local.alert_every
  alarm_name          = "Budget_${(count.index+1) * local.alert_every}"
  alarm_description   = "We've now spent ${"$"}${(count.index+1) * local.alert_every}"
  threshold           = (count.index+1) * local.alert_every
  period              = 6 * 60 * 60
  evaluation_periods  = 1
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  dimensions          = {
    Currency = "USD"
  }
  alarm_actions = [data.aws_sns_topic.alert.arn]
}

resource "aws_cloudwatch_metric_alarm" "budget_hit" {
  alarm_name          = "BudgetHit"
  alarm_description   = "We've hit the budget"
  threshold           = local.approx_monthly_budget
  period              = 6 * 60 * 60
  evaluation_periods  = 1
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  comparison_operator = "GreaterThanThreshold"
  statistic           = "Maximum"
  dimensions          = {
    Currency = "USD"
  }
  alarm_actions = [data.aws_sns_topic.alert.arn]
}

resource "aws_cloudwatch_metric_alarm" "bankrupcy" {
  alarm_name          = "EmergencyBillingAlert"
  alarm_description   = "Is Matt about to have to sell his house?"
  threshold           = local.approx_monthly_budget * 1.5
  period              = 6 * 60 * 60
  evaluation_periods  = 1
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  comparison_operator = "GreaterThanThreshold"
  statistic           = "Maximum"
  dimensions          = {
    Currency = "USD"
  }
  alarm_actions = [data.aws_sns_topic.alert.arn]
}

resource "aws_cloudwatch_metric_alarm" "cloudfront_high_5xx" {
  for_each = {
    "godbolt.org"           = aws_cloudfront_distribution.ce-godbolt-org,
    "compiler-explorer.com" = aws_cloudfront_distribution.compiler-explorer-com,
    "godbo.lt"              = aws_cloudfront_distribution.godbo-lt,
    "ce.cdn.net"            = aws_cloudfront_distribution.static-ce-cdn-net
  }
  alarm_name          = "High5xx_${each.key}"
  alarm_description   = "Unnacceptable level of 5xx errors on ${each.key} (once we have enough actual queries)"
  evaluation_periods  = 4
  datapoints_to_alarm = 4
  threshold           = 3

  metric_query {
    id          = "errors_once_over_threshold"
    expression  = "IF(total_requests_per_5m > 100, error_rate, 0)"
    label       = "Error rate (assuming we have enough traffic)"
    return_data = true
  }


  metric_query {
    id = "error_rate"
    metric {
      metric_name = "5xxErrorRate"
      namespace   = "AWS/CloudFront"
      stat        = "Average"
      period      = 5 * 60
      dimensions  = {
        DistributionId = each.value.id
        Region         = "Global"
      }
    }
  }

  metric_query {
    id = "total_requests_per_5m"
    metric {
      metric_name = "Requests"
      namespace   = "AWS/CloudFront"
      stat        = "Sum"
      period      = 5 * 60
      dimensions  = {
        DistributionId = each.value.id
        Region         = "Global"
      }
    }
  }

  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [data.aws_sns_topic.alert.arn]
}


resource "aws_cloudwatch_metric_alarm" "traffic" {
  alarm_name          = "TrafficAnomaly"
  alarm_description   = "A traffic anomaly was detected (too much or too little)"
  evaluation_periods  = 10
  datapoints_to_alarm = 7
  threshold_metric_id = "e1"

  metric_query {
    id          = "e1"
    expression  = "ANOMALY_DETECTION_BAND(m1,10)"
    label       = "RequestCount (Expected)"
    return_data = true
  }

  metric_query {
    id          = "m1"
    return_data = true
    metric {
      metric_name = "RequestCount"
      namespace   = "AWS/ApplicationELB"
      period      = 5*60
      stat        = "Sum"
      dimensions  = {
        LoadBalancer = aws_alb.GccExplorerApp.arn_suffix
      }
    }
  }

  comparison_operator = "LessThanLowerOrGreaterThanUpperThreshold"
  alarm_actions       = [data.aws_sns_topic.alert.arn]
}

resource "aws_cloudwatch_metric_alarm" "high_traffic" {
  alarm_name          = "Traffic"
  alarm_description   = "A high amount of traffic: did we just get slashdotted?"
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 7500
  metric_name         = "RequestCount"
  namespace           = "AWS/ApplicationELB"
  statistic           = "Sum"
  period              = 5 * 60

  dimensions = {
    LoadBalancer = aws_alb.GccExplorerApp.arn_suffix
  }
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [data.aws_sns_topic.alert.arn]
}

resource "aws_cloudwatch_metric_alarm" "efs_burst_credit" {
  alarm_name         = "EFS burst credit"
  alarm_description  = "Making sure we have lots of EFS performance in the bank"
  evaluation_periods = 1
  period             = 60
  namespace          = "AWS/EFS"
  metric_name        = "BurstCreditBalance"
  statistic          = "Minimum"
  dimensions         = {
    FileSystemId = aws_efs_file_system.fs-db4c8192.id
  }
  threshold           = 20000000000
  comparison_operator = "LessThanOrEqualToThreshold"
  alarm_actions       = [data.aws_sns_topic.alert.arn]
}

resource "aws_cloudwatch_metric_alarm" "no_prod_nodes" {
  alarm_name         = "NoHealthyProdNodes"
  alarm_description  = "Ensure there's at least one healthy node in production"
  evaluation_periods = 1
  period             = 60
  namespace          = "AWS/AutoScaling"
  metric_name        = "GroupInServiceInstances"
  statistic          = "Minimum"
  dimensions         = {
    AutoScalingGroupName = aws_autoscaling_group.prod-mixed.name
  }

  threshold           = 1
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [data.aws_sns_topic.alert.arn]
}

resource "aws_cloudwatch_metric_alarm" "waf_throttled" {
  alarm_name         = "WafIsThrottling"
  alarm_description  = "We're seeing some amount of WAF client throttling, which may indicate a DoS or a WAF rate limit too low"
  evaluation_periods = 1
  period             = 60
  namespace          = "AWS/WAFV2"
  metric_name        = "BlockedRequests"
  statistic          = "Maximum"
  dimensions         = {
    WebACL = aws_wafv2_web_acl.compiler-explorer.name
    Rule   = local.deny_rate_limit_name_metric_name
  }

  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [data.aws_sns_topic.alert.arn]
}
