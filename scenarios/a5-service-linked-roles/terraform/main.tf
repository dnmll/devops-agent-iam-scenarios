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
      "iamscn:scenario" = "a5-service-linked-roles"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply the scenario.yaml substitutions (account id, region) to the raw
  # customer artifact. Nothing else is rewritten.
  slr_policy_json = replace(
    replace(
      file("${path.module}/../policies/create-slr-policy.json"),
      "111122223333", data.aws_caller_identity.current.account_id
    ),
    "us-east-1", var.region
  )
}

# ---------------------------------------------------------------------------
# IAM primitives only. The two service-linked roles this policy can create are
# deliberately NOT provisioned here: they are account-wide singletons that the
# per-run `terraform destroy` must never delete (see
# docs/sandbox-account.md → sweeper allowlist, and the scenario README →
# "Never delete these"). Every probe is `kind: simulate`, so the only thing that
# has to exist is a role carrying the artifact as its candidate policy.
#
# The permissions boundary caps this role as it does every iamscn-* role. The
# boundary's KnownServiceLinkedRolesOnly statement already allows both
# aidevops.amazonaws.com and delivery.logs.amazonaws.com, so the grant survives
# the cap — and the simulate probes evaluate the artifact alone anyway (no
# boundary intersection; see .claude/rules/validation.md).
# ---------------------------------------------------------------------------
module "slr_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-a5-slr"
  scenario         = "a5-service-linked-roles"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.slr_policy_json
  boundary_arn     = var.boundary_arn
}
