resource "aws_cloudfront_distribution" "conan-compiler-explorer-com" {
  origin {
    domain_name = "compiler-explorer.s3.amazonaws.com"
    origin_id   = "S3-compiler-explorer"
  }
  origin {
    domain_name = aws_alb.GccExplorerApp.dns_name
    origin_id   = "GccExplorerApp"
    custom_origin_config {
      http_port                = 1080
      https_port               = 1443
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
      origin_protocol_policy   = "https-only"
      origin_ssl_protocols     = [
        "TLSv1",
        "TLSv1.2",
        "TLSv1.1"
      ]
    }
  }

  enabled          = true
  is_ipv6_enabled  = true
  retain_on_delete = true
  aliases          = [
    "conan.compiler-explorer.com"
  ]

  viewer_certificate {
    acm_certificate_arn      = data.aws_acm_certificate.godbolt-org-et-al.arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.1_2016"
  }

  logging_config {
    include_cookies = false
    bucket          = "compiler-explorer-logs.s3.amazonaws.com"
    prefix          = "cloudfront/"
  }

  http_version = "http2"

  restrictions {
    geo_restriction {
      restriction_type = "blacklist"
      locations        = [
        "CU",
        "IR",
        "KP",
        "SD",
        "SY"
      ]
    }
  }

  default_cache_behavior {
    allowed_methods = [
      "HEAD",
      "DELETE",
      "POST",
      "GET",
      "OPTIONS",
      "PUT",
      "PATCH"
    ]
    cached_methods = [
      "HEAD",
      "GET"
    ]
    forwarded_values {
      cookies {
        forward = "all"
      }
      query_string = true
      headers      = [
        "Accept",
        "Host",
        "Authorization"
      ]
    }
    target_origin_id       = "GccExplorerApp"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
  }
}
