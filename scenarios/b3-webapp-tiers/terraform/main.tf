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
      "iamscn:scenario" = "b3-webapp-tiers"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply scenario.yaml substitutions to the raw customer artifacts. The tiers
  # carry no role-name placeholders, so account id + region are the whole map.
  tiers = {
    admin    = "admin-policy.json"
    operator = "operator-policy.json"
    readonly = "readonly-policy.json"
  }

  policy_json = {
    for tier, policy_file in local.tiers : tier => replace(
      replace(
        file("${path.module}/../policies/${policy_file}"),
        "111122223333", data.aws_caller_identity.current.account_id
      ),
      "us-east-1", var.region
    )
  }
}

# One role per tier: assumable by the CI role, capped by the permissions boundary.
module "tier_role" {
  source           = "../../../terraform/modules/scenario-role"
  for_each         = local.tiers
  name             = "iamscn-b3-${each.key}"
  scenario         = "b3-webapp-tiers"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.policy_json[each.key]
  boundary_arn     = var.boundary_arn
}
