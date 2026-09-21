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
    # break planning ("inconsistent final plan").
    tags = {
      "iamscn:scenario" = "a4-operator-webapp-role"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply the scenario.yaml substitutions (account id, region) to the raw
  # customer artifacts. `file()` returns the file's bytes — terraform does NOT
  # interpolate them — and `replace()` here is literal string replacement, so
  # the policy variables "${aws:PrincipalTag/AgentSpaceId}" and
  # "${aws:PrincipalAccount}" in the permission policy survive verbatim. Never
  # rebuild these documents with jsonencode()/templatefile(): templatefile()
  # *would* try to evaluate ${aws:...} as HCL and fail the plan.
  trust_policy_json = replace(
    replace(
      file("${path.module}/../policies/trust-policy.json"),
      "111122223333", data.aws_caller_identity.current.account_id
    ),
    "us-east-1", var.region
  )

  # operator-app-policy.json contains no account id and no region (the
  # documented aidevops Resource wildcards both: arn:aws:aidevops:*:*:...), so
  # the same substitution pair is a no-op on it. It is applied anyway, so the
  # harness stays uniform and a future narrowing of the ARN picks it up.
  operator_app_policy_json = replace(
    replace(
      file("${path.module}/../policies/operator-app-policy.json"),
      "111122223333", data.aws_caller_identity.current.account_id
    ),
    "us-east-1", var.region
  )
}

# ---------------------------------------------------------------------------
# The deliverable: the Operator Web App role. Trust policy verbatim (so what IAM
# stores is byte-for-byte the shipped document with placeholders resolved), and
# the operator-app policy attached as an inline policy — also verbatim, which is
# the live evidence that IAM accepts the session-tag policy variable in a
# Resource ARN. Customers who do not need to review or narrow the document can
# attach the AWS managed policy AIDevOpsOperatorAppAccessPolicy by ARN instead;
# this artifact is its customer-managed equivalent (see README).
#
# Note the permissions boundary: every iamscn-* role carries iamscn-boundary as
# a sandbox blast-radius cap. It is NOT part of the customer deliverable, and
# its union covers aidevops only — so in the sandbox the support /
# secretsmanager / transcribe grants are capped away. That is why this scenario
# has no `real` probes; the artifact-fidelity verdict comes from
# iam:SimulateCustomPolicy (no boundary) and from the static assertions.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "operator_app" {
  name                 = "iamscn-a4-operator-app"
  permissions_boundary = var.boundary_arn
  assume_role_policy   = local.trust_policy_json
  tags = {
    "iamscn:scenario" = "a4-operator-webapp-role"
  }
}

resource "aws_iam_role_policy" "operator_app" {
  name   = "iamscn-a4-operator-app-access"
  role   = aws_iam_role.operator_app.id
  policy = local.operator_app_policy_json
}

# ---------------------------------------------------------------------------
# Probe anchor. The role above is assumable by aidevops.amazonaws.com ONLY (that
# is what its trust policy says, and widening it to let the CI role in would
# stop testing the deliverable), so the probe runner cannot assume it. This
# second role carries the same permission policy as its candidate policy and is
# what `role_under_test: operator_role_arn` points at; the output name also
# selects policies/operator-app-policy.json as the simulated artifact, keeping
# the trust document (not an identity policy) out of PolicyInputList.
# ---------------------------------------------------------------------------
module "operator_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-a4-operator"
  scenario         = "a4-operator-webapp-role"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.operator_app_policy_json
  boundary_arn     = var.boundary_arn
}
