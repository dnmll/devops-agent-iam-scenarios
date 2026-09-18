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
      "iamscn:scenario" = "b5-customer-kms-key"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply the scenario.yaml substitutions to the raw customer artifact: account
  # id and region. Only the caller *identity* policy is deployed —
  # policies/key-policy.json is a KMS resource policy and this harness
  # deliberately creates no KMS key (see the scenario README).
  caller_policy_json = replace(
    replace(
      file("${path.module}/../policies/caller-policy.json"),
      "111122223333", data.aws_caller_identity.current.account_id
    ),
    "us-east-1", var.region
  )
}

# The identity under test: assumable by the CI role, capped by the boundary.
module "caller_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-b5-caller"
  scenario         = "b5-customer-kms-key"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.caller_policy_json
  boundary_arn     = var.boundary_arn
}
