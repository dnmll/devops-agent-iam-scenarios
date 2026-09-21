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
  # One artifact per service-linked role: each correlates a single SLR ARN with
  # its own iam:AWSServiceName, which is what makes the cross combinations (one
  # SLR's ARN carrying the other's service name) implicitly denied.
  artifacts = [
    "create-metrics-slr-policy.json",
    "create-log-delivery-slr-policy.json",
  ]

  # Apply the scenario.yaml substitutions (account id, region) to the raw
  # customer artifacts. Nothing else is rewritten.
  artifact_json = [
    for policy_file in local.artifacts : replace(
      replace(
        file("${path.module}/../policies/${policy_file}"),
        "111122223333", data.aws_caller_identity.current.account_id
      ),
      "us-east-1", var.region
    )
  ]

  # A customer attaches both artifacts together, so the single probe-anchor role
  # carries both: their statements concatenated verbatim into one role policy.
  # The simulate probes evaluate the same union (run_probes.simulated_artifacts
  # returns all artifacts here — the tier-matching path needs a role_under_test
  # naming one artifact, and `slr_role_arn` deliberately names neither).
  slr_policy_json = jsonencode({
    Version   = "2012-10-17"
    Statement = flatten([for doc in local.artifact_json : jsondecode(doc)["Statement"]])
  })
}

# ---------------------------------------------------------------------------
# IAM primitives only. The two service-linked roles these policies can create
# are deliberately NOT provisioned here: they are account-wide singletons that
# the per-run `terraform destroy` must never delete (see
# docs/sandbox-account.md → sweeper allowlist, and the scenario README →
# "Never delete these"). Every probe is `kind: simulate`, so the only thing that
# has to exist is one role carrying both artifacts as its candidate policy.
#
# The permissions boundary caps this role as it does every iamscn-* role. The
# boundary's KnownServiceLinkedRolesOnly statement already allows both
# aidevops.amazonaws.com and delivery.logs.amazonaws.com, so the grants survive
# the cap — and the simulate probes evaluate the artifacts alone anyway (no
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
