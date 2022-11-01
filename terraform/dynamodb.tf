resource "aws_dynamodb_table" "links" {
  name = "links"
  lifecycle {
    ignore_changes = [
      read_capacity,
      write_capacity
    ]
    prevent_destroy = true
  }
  billing_mode   = "PAY_PER_REQUEST"
  read_capacity  = 1
  write_capacity = 1
  hash_key       = "prefix"
  range_key      = "unique_subhash"

  attribute {
    name = "prefix"
    type = "S"
  }
  attribute {
    name = "unique_subhash"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_dynamodb_table" "versionslog" {
  name = "versionslog"
  lifecycle {
    ignore_changes = [
      read_capacity,
      write_capacity
    ]
    prevent_destroy = true
  }
  billing_mode   = "PAY_PER_REQUEST"
  read_capacity  = 1
  write_capacity = 1
  hash_key       = "buildId"
  range_key      = "timestamp"

  attribute {
    name = "buildId"
    type = "S"
  }
  attribute {
    name = "timestamp"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_dynamodb_table" "compiler-builds" {
  name = "compiler-builds"
  lifecycle {
    ignore_changes = [
      read_capacity,
      write_capacity
    ]
    prevent_destroy = true
  }
  billing_mode   = "PAY_PER_REQUEST"
  read_capacity  = 1
  write_capacity = 1
  hash_key       = "compiler"
  range_key      = "timestamp"

  attribute {
    name = "compiler"
    type = "S"
  }
  attribute {
    name = "timestamp"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}
