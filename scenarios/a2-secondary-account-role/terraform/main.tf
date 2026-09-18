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
      "iamscn:scenario" = "a2-secondary-account-role"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply the scenario.yaml substitutions to the raw customer artifacts. Only the
  # MEMBER-account placeholder (111122223333) and the Region are rewritten: the
  # sandbox account plays the secondary/source account, which is where this role
  # and its Resource Explorer SLR live.
  #
  # 222233334444 — the monitoring (primary) account in the trust policy's
  # confused-deputy conditions — is deliberately left as-is. Rewriting it to the
  # sandbox account id would turn this artifact back into a1's same-account trust
  # policy and the scenario would stop testing anything cross-account.
  # aws:SourceAccount / aws:SourceArn are plain string conditions, so IAM stores
  # a foreign account id in them without validating that the account exists —
  # which is exactly what makes `terraform apply` live evidence that a genuinely
  # cross-account trust document is accepted verbatim.
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
# The deliverable: the cross-account role as step 4 of the CLI onboarding guide
# creates it in the secondary account (`DevOpsAgentCrossAccountRole`) — trust
# policy verbatim, AIDevOpsAgentAccessPolicy attached BY ARN (never inlined: it
# is an AWS-owned, 30KB+, AWS-revised document), plus the one inline grant the
# managed policy does not carry, scoped to THIS account's SLR path.
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
resource "aws_iam_role" "secondary" {
  name                 = "iamscn-a2-secondary"
  permissions_boundary = var.boundary_arn
  assume_role_policy   = local.trust_policy_json
  tags = {
    "iamscn:scenario" = "a2-secondary-account-role"
  }
}

resource "aws_iam_role_policy_attachment" "agent_access" {
  role       = aws_iam_role.secondary.name
  policy_arn = "arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy"
}

resource "aws_iam_role_policy" "secondary_slr" {
  name   = "iamscn-a2-resource-explorer-slr"
  role   = aws_iam_role.secondary.id
  policy = local.slr_policy_json
}

# ---------------------------------------------------------------------------
# Probe anchor. The role above is assumable by aidevops.amazonaws.com ONLY, and
# only when the call originates from the monitoring account named in its
# conditions — the CI role can never assume it, and adding the CI role to its
# trust policy would mean the harness no longer deploys the deliverable. This
# second role carries the same inline SLR policy as its candidate policy and is
# what `role_under_test: slr_role_arn` points at; every probe in this scenario is
# `kind: simulate`, evaluated against policies/slr-inline-policy.json.
# ---------------------------------------------------------------------------
module "slr_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-a2-slr"
  scenario         = "a2-secondary-account-role"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.slr_policy_json
  boundary_arn     = var.boundary_arn
}
