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
      "iamscn:scenario" = "a1-agentspace-role"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply the scenario.yaml substitutions (account id, region) to the raw
  # customer artifacts. Nothing else is rewritten: the trust document below is
  # deployed VERBATIM, so what AWS stores as this role's assume-role policy is
  # byte-for-byte the shipped deliverable with the placeholders resolved. That
  # is the point of this harness — a trust policy cannot be simulated
  # (iam:SimulateCustomPolicy takes identity policies only), so "IAM accepted
  # it, with both confused-deputy conditions intact" is the live evidence.
  trust_policy_json = replace(
    replace(
      file("${path.module}/../policies/trust-policy.json"),
      "111122223333", data.aws_caller_identity.current.account_id
    ),
    "us-east-1", var.region
  )

  slr_policy_json = replace(
    replace(
      file("${path.module}/../policies/slr-inline-policy.json"),
      "111122223333", data.aws_caller_identity.current.account_id
    ),
    "us-east-1", var.region
  )
}

# ---------------------------------------------------------------------------
# The deliverable: the Agent Space role exactly as the CLI onboarding guide
# creates it — trust policy verbatim, AIDevOpsAgentAccessPolicy attached BY ARN
# (never inlined: it is an AWS-owned, 30KB+, AWS-revised document), plus the one
# inline grant the managed policy does not carry.
#
# Note the permissions boundary: every iamscn-* role carries iamscn-boundary, so
# in the sandbox this role's effective permissions are
# AIDevOpsAgentAccessPolicy ∩ iamscn-boundary — a sandbox blast-radius cap, NOT
# part of the customer deliverable (customers attach no boundary here). The
# boundary's KnownServiceLinkedRolesOnly statement already allows
# resource-explorer-2.amazonaws.com, so the inline SLR grant survives the cap.
# ---------------------------------------------------------------------------
# assume_role_policy is the artifact VERBATIM (substitutions only) — never
# rebuild it with jsonencode().
resource "aws_iam_role" "agentspace" {
  name                 = "iamscn-a1-agentspace"
  permissions_boundary = var.boundary_arn
  assume_role_policy   = local.trust_policy_json
  tags = {
    "iamscn:scenario" = "a1-agentspace-role"
  }
}

resource "aws_iam_role_policy_attachment" "agent_access" {
  role       = aws_iam_role.agentspace.name
  policy_arn = "arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy"
}

resource "aws_iam_role_policy" "agentspace_slr" {
  name   = "iamscn-a1-resource-explorer-slr"
  role   = aws_iam_role.agentspace.id
  policy = local.slr_policy_json
}

# ---------------------------------------------------------------------------
# Probe anchor. The role above is assumable by aidevops.amazonaws.com ONLY (that
# is what its trust policy says, and weakening it to let the CI role in would
# stop testing the deliverable), so the probe runner cannot assume it. This
# second role carries the same inline SLR policy as its candidate policy and is
# what `role_under_test: slr_role_arn` points at; every probe in this scenario is
# `kind: simulate`, evaluated against policies/slr-inline-policy.json.
# ---------------------------------------------------------------------------
module "slr_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-a1-slr"
  scenario         = "a1-agentspace-role"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.slr_policy_json
  boundary_arn     = var.boundary_arn
}
