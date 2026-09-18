terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      "iamscn:scenario" = "b2-installer"
      "iamscn:run-id"   = var.run_id
      "iamscn:expiry"   = timeadd(timestamp(), "4h")
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply scenario.yaml substitutions to the raw customer artifact.
  policy_json = replace(
    replace(
      replace(
        file("${path.module}/../policies/installer-policy.json"),
        "111122223333", data.aws_caller_identity.current.account_id
      ),
      "us-east-1", var.region
    ),
    "DevOpsAgentRole-", "iamscn-b2-dar-"
  )
}

# The role under test: assumable by the CI role, capped by the permissions boundary.
module "installer_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-b2-installer"
  scenario         = "b2-installer"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.policy_json
  boundary_arn     = var.boundary_arn
}

# A passable target so iam:PassRole probes are real: mimics the Agent Space role
# (trusts aidevops.amazonaws.com) under the substituted DevOpsAgentRole- prefix.
resource "aws_iam_role" "agentspace_target" {
  name                 = "iamscn-b2-dar-agentspace"
  permissions_boundary = var.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "aidevops.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}
