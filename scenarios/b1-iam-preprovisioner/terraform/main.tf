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
    # Must be static values: dynamic expressions (e.g. timestamp()) in
    # default_tags break planning ("inconsistent final plan"). Resource age for
    # the sweeper comes from CreateDate, not a tag.
    tags = {
      "iamscn:scenario" = "b1-iam-preprovisioner"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply scenario.yaml substitutions to the raw customer artifact.
  policy_json = replace(
    replace(
      replace(
        file("${path.module}/../policies/preprovisioner-policy.json"),
        "111122223333", data.aws_caller_identity.current.account_id
      ),
      "us-east-1", var.region
    ),
    "DevOpsAgentRole-", "iamscn-b1-dar-"
  )
}

# The role under test: assumable by the CI role, capped by the permissions boundary.
module "preprovisioner_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-b1-preprovisioner"
  scenario         = "b1-iam-preprovisioner"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.policy_json
  boundary_arn     = var.boundary_arn
}

# An existing DevOpsAgentRole-prefixed role (substituted to iamscn-b1-dar-*) so
# the read/update/delete half of the lifecycle simulates against a real ARN.
# Trusts aidevops.amazonaws.com exactly as the CLI onboarding guide's Agent Space
# role does; the pre-provisioner never assumes or passes it.
resource "aws_iam_role" "preprovisioned_target" {
  name                 = "iamscn-b1-dar-agentspace"
  permissions_boundary = var.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "aidevops.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:aidevops:${var.region}:${data.aws_caller_identity.current.account_id}:agentspace/*" }
      }
    }]
  })
}
