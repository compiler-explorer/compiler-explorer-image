resource "aws_route53_zone" "zone" {
  name    = var.domain_name
  comment = "Compiler Explorer for ${var.domain_name}"
}

resource "aws_route53_record" "address" {
  for_each = {
    a    = "A"
    aaaa = "AAAA"
  }
  zone_id = aws_route53_zone.zone.zone_id
  name    = var.top_level_name
  type    = each.value
  alias {
    name                   = var.cloudfront_distribution.domain_name
    zone_id                = var.cloudfront_distribution.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "acm" {
  for_each = {
  for dvo in var.certificate.domain_validation_options : dvo.domain_name => {
    name   = dvo.resource_record_name
    record = dvo.resource_record_value
    type   = dvo.resource_record_type
  }
  }

  allow_overwrite = true
  name            = each.value.name
  zone_id         = aws_route53_zone.zone.zone_id
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
}

resource "aws_route53_record" "wildcard" {
  count   = var.wildcard ? 1 : 0
  zone_id = aws_route53_zone.zone.zone_id
  name    = "*"
  type    = "CNAME"
  ttl     = 60
  records = [aws_route53_record.address["a"].fqdn]
}

resource "aws_route53_record" "spf" {
  count   = var.mail ? 1 : 0
  zone_id = aws_route53_zone.zone.zone_id
  name    = ""
  type    = "TXT"
  ttl     = 3600
  records = [
    "v=spf1 include:_spf.google.com ~all",
    "v=DMARC1;p=none;sp=quarantine;rua=mailto:matt+dmarc@${var.domain_name}",
    "google-site-verification=uCqzvXJNW3IV25ZPjOmXyrTBA_dwzpo57znHNWU11s0"
  ]
}

resource "aws_route53_record" "mail" {
  count   = var.mail ? 1 : 0
  name    = ""
  type    = "MX"
  ttl     = 3600
  zone_id = aws_route53_zone.zone.zone_id
  records = [
    "1 aspmx.l.google.com",
    "5 alt1.aspmx.l.google.com",
    "5 alt2.aspmx.l.google.com",
    "10 alt3.aspmx.l.google.com",
    "10 alt4.aspmx.l.google.com",
  ]
}
