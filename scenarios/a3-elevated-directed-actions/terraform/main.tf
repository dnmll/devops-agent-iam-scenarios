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
      "iamscn:scenario" = "a3-elevated-directed-actions"
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
  # is the live evidence for an artifact a simulator cannot evaluate — and for
  # this scenario it is the evidence that matters most, because a trust policy
  # missing sts:SetSourceIdentity or sts:TagSession validates as `valid` on the
  # association and only fails later, at credential time.
  trust_policy_json = replace(
    replace(
      file("${path.module}/../policies/trust-policy.json"),
      "111122223333", data.aws_caller_identity.current.account_id
    ),
    "us-east-1", var.region
  )

  remediation_policy_json = replace(
    replace(
      file("${path.module}/../policies/example-remediation-policy.json"),
      "111122223333", data.aws_caller_identity.current.account_id
    ),
    "us-east-1", var.region
  )
}

# ---------------------------------------------------------------------------
# The deliverable: the elevated role as "Working with directed actions" creates
# it — trust policy verbatim (all three STS actions), with the worked example
# remediation policy as its inline permission half.
#
# IAM primitives only. The harness creates no EC2 instance and no Lambda
# function: every probe is `kind: simulate`, and provisioning something for the
# agent to reboot would give this sandbox role real blast radius for no added
# evidence. Registering the role on an association (`agentElevatedRoleArn`) is
# the installer's API call, never terraform's.
#
# Note the permissions boundary: every iamscn-* role carries iamscn-boundary, so
# in the sandbox this role's effective permissions are
# example-remediation-policy ∩ iamscn-boundary = nothing (the boundary's union is
# aidevops/scoped-IAM only; it grants no ec2 or lambda). That is a sandbox
# blast-radius cap, NOT part of the customer deliverable, and it is exactly why
# the probes use iam:SimulateCustomPolicy against the artifact instead of
# iam:SimulatePrincipalPolicy against the deployed role.
# ---------------------------------------------------------------------------
# assume_role_policy is the artifact VERBATIM (substitutions only) — never
# rebuild it with jsonencode().
resource "aws_iam_role" "elevated" {
  name                 = "iamscn-a3-elevated"
  permissions_boundary = var.boundary_arn
  assume_role_policy   = local.trust_policy_json
  tags = {
    "iamscn:scenario" = "a3-elevated-directed-actions"
  }
}

resource "aws_iam_role_policy" "elevated_remediation" {
  name   = "iamscn-a3-example-remediation"
  role   = aws_iam_role.elevated.id
  policy = local.remediation_policy_json
}

# ---------------------------------------------------------------------------
# Probe anchor. The role above is assumable by aidevops.amazonaws.com ONLY (that
# is what its trust policy says, and weakening it to let the CI role in would
# stop testing the deliverable), so the probe runner cannot assume it. This
# second role carries the same example remediation policy as its candidate
# policy and is what `role_under_test: remediation_role_arn` points at; every
# probe in this scenario is `kind: simulate`, evaluated against
# policies/example-remediation-policy.json.
# ---------------------------------------------------------------------------
module "remediation_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-a3-remediation"
  scenario         = "a3-elevated-directed-actions"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.remediation_policy_json
  boundary_arn     = var.boundary_arn
}
