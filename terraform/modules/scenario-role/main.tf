terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

variable "name" { type = string }
variable "scenario" { type = string }
variable "assume_role_arns" { type = list(string) }
variable "policy_json" { type = string }
variable "boundary_arn" { type = string }

resource "aws_iam_role" "this" {
  name                 = var.name
  permissions_boundary = var.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = var.assume_role_arns }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = {
    "iamscn:scenario" = var.scenario
  }
}

resource "aws_iam_role_policy" "candidate" {
  name   = "${var.name}-candidate-policy"
  role   = aws_iam_role.this.id
  policy = var.policy_json
}

output "role_arn" {
  value = aws_iam_role.this.arn
}

output "role_name" {
  value = aws_iam_role.this.name
}
