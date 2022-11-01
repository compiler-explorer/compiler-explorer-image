variable "cidr_b_prefix" {
  description = "The CIDR Type B prefix (e.g. '172.30') to create"
  type        = string
}

variable "subnets" {
  description = "The subnets to run inside - map of name to third octet"
  type        = map(string)
}
