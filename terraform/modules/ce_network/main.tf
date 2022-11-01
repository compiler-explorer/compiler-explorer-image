resource "aws_vpc" "CompilerExplorer" {
  cidr_block           = "${var.cidr_b_prefix}.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  instance_tenancy     = "default"

  tags = {
    Name = "CompilerExplorer"
  }
}

resource "aws_internet_gateway" "ce-gw" {
  vpc_id = aws_vpc.CompilerExplorer.id

  tags = {
    Name = "CompilerExplorerVpcGw"
  }
}

resource "aws_default_route_table" "ce-route-table" {
  default_route_table_id = aws_vpc.CompilerExplorer.default_route_table_id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.ce-gw.id
  }

  tags = {
    Name = "NodeRouteTable"
  }
}

resource "aws_subnet" "ce" {
  for_each                = var.subnets
  vpc_id                  = aws_vpc.CompilerExplorer.id
  cidr_block              = "${var.cidr_b_prefix}.${each.value}.0/24"
  availability_zone       = "us-east-${each.key}"
  map_public_ip_on_launch = true

  tags = {
    Name = "CompilerExplorer${each.key}"
  }
}
