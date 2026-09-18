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
    # Static values only: dynamic expressions (e.g. timestamp()) in default_tags
    # break planning ("inconsistent final plan"). Resource age for the sweeper
    # comes from CreateDate, not a tag.
    tags = {
      "iamscn:scenario" = "b6-cicd-deployer"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply the scenario.yaml substitutions to the raw customer artifact: account
  # id, region, and the passable-role prefix (the harness keeps everything it
  # creates inside the iamscn- namespace).
  policy_json = replace(
    replace(
      replace(
        file("${path.module}/../policies/cicd-deployer-policy.json"),
        "111122223333", data.aws_caller_identity.current.account_id
      ),
      "us-east-1", var.region
    ),
    "DevOpsAgentRole-", "iamscn-b6-dar-"
  )
}

# The pipeline identity under test: assumable by the CI role, capped by the
# boundary. Note that the awscc provider is deliberately NOT used here — this
# harness provisions IAM primitives only and the probes are simulate-only
# (docs/decisions.md D1, and see the scenario README on what that leaves
# unverified).
module "deployer_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-b6-deployer"
  scenario         = "b6-cicd-deployer"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.policy_json
  boundary_arn     = var.boundary_arn
}

# A passable target so the iam:PassRole probes name a real ARN: mimics the Agent
# Space role the awscc configuration hands to the service (trusts
# aidevops.amazonaws.com) under the substituted DevOpsAgentRole- prefix. The
# deployer can pass it; it never creates, assumes or edits it (that is b1).
resource "aws_iam_role" "passable_agentspace_role" {
  name                 = "iamscn-b6-dar-agentspace"
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
