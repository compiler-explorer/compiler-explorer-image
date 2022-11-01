resource "aws_acm_certificate" "static-ce-cdn-net" {
  domain_name       = "static.ce-cdn.net"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

/*
Disabled because cert was manually created in AWS console, see the below terraform bug

https://github.com/terraform-providers/terraform-provider-aws/issues/8597

resource "aws_acm_certificate_validation" "static-ce-cdn-net" {
  certificate_arn         = "${aws_acm_certificate.static-ce-cdn-net.arn}"
  validation_record_fqdns = ["${aws_route53_record.static-ce-cdn-net-acm.fqdn}"]
}
*/

resource "aws_acm_certificate" "godbolt-org-et-al" {
  domain_name       = "godbolt.org"
  validation_method = "DNS"

  subject_alternative_names = [
    "*.godbo.lt",
    "*.compiler-explorer.com",
    "godbo.lt",
    "compiler-explorer.com",
    "*.godbolt.org"
  ]
  lifecycle {
    create_before_destroy = true
  }
}
