# ---------------------------------------------------------------------------
# live-agentspace — long-lived prerequisites for a REAL, human-usable Agent
# Space. Applied BY HAND by an operator; never by CI (see README.md).
#
# Creates IAM + KMS + one CloudWatch Logs group only. The Agent Space itself is
# created with the CLI (decision D1 in docs/decisions.md: no awscc/Cloud Control
# coverage for aidevops resource types), consuming the outputs of this module.
#
# Three constraints this file exists to honour — read README.md before editing:
#   1. No `iamscn-` names (tools/probes/sweeper.py deletes aged iamscn-* roles
#      AND Agent Spaces). Everything is named `${var.name_prefix}…`, default
#      `DevOpsAgentRole-`, which also matches b2's iam:PassRole scope.
#   2. No permissions boundary. `iamscn-boundary` caps policies *under test* to
#      aidevops:* plus narrow IAM reads; AIDevOpsAgentAccessPolicy needs broad
#      describe/read across many services, so a boundary-capped Agent Space role
#      deploys fine and then fails every investigation.
#   3. The CMK key policy carries a key-administration statement (kms.tf).
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  agentspace_role_name    = "${var.name_prefix}agentspace"
  operator_app_role_name  = "${var.name_prefix}operator-app"
  installer_role_name     = "${var.name_prefix}installer"
  log_group_name          = "/aws/vendedlogs/devops-agent/${var.agentspace_name}"
  scenario_policies       = "${path.module}/../../scenarios"
  aidevops_via_service    = "aidevops.${var.region}.amazonaws.com"
  agentspace_arn_wildcard = "arn:${local.partition}:aidevops:${var.region}:${local.account_id}:agentspace/*"
  service_arn_wildcard    = "arn:${local.partition}:aidevops:${var.region}:${local.account_id}:service/*"
}

# The scenario artifacts under scenarios/ are the single source of truth for
# every statement in this module — nothing here invents IAM. Only the documented
# placeholders are rewritten (docs/decisions.md D2): account id, Region, and b5's
# example key ARN.
locals {
  a1_trust_policy = replace(
    replace(file("${local.scenario_policies}/a1-agentspace-role/policies/trust-policy.json"),
    "111122223333", local.account_id),
    "us-east-1", var.region
  )

  a1_slr_policy = replace(
    replace(file("${local.scenario_policies}/a1-agentspace-role/policies/slr-inline-policy.json"),
    "111122223333", local.account_id),
    "us-east-1", var.region
  )

  a4_trust_policy = replace(
    replace(file("${local.scenario_policies}/a4-operator-webapp-role/policies/trust-policy.json"),
    "111122223333", local.account_id),
    "us-east-1", var.region
  )

  b2_installer_policy = replace(
    replace(file("${local.scenario_policies}/b2-installer/policies/installer-policy.json"),
    "111122223333", local.account_id),
    "us-east-1", var.region
  )

  # b5's caller policy pins the five crypto actions to ONE key ARN; the example
  # key id in the artifact is the third documented placeholder (see b5's README:
  # "replace 1234abcd-… with your key id") and resolves to the key created here.
  # Substituting the id rather than the whole ARN keeps this working if b5's
  # example account/Region ever change.
  b5_caller_policy = replace(
    replace(
      replace(file("${local.scenario_policies}/b5-customer-kms-key/policies/caller-policy.json"),
      "111122223333", local.account_id),
      "us-east-1", var.region
    ),
    "1234abcd-12ab-34cd-56ef-1234567890ab",
    aws_kms_key.agentspace.key_id
  )

  # b7's CloudWatch-Logs target, re-pointed at the log group created here. The
  # artifact's grant is prefix-scoped to /aws/vendedlogs/devops-agent/*, which
  # local.log_group_name already sits under, so only the placeholders change.
  b7_logs_target_policy = replace(
    replace(file("${local.scenario_policies}/b7-log-delivery/policies/cloudwatch-logs-target.json"),
    "111122223333", local.account_id),
    "us-east-1", var.region
  )
}

# ---------------------------------------------------------------------------
# Agent Space role — a1 shape.
# Trust policy deployed VERBATIM (placeholders resolved only), so the confused-
# deputy pair (aws:SourceAccount + ArnLike aws:SourceArn on agentspace/*) is
# exactly the shipped deliverable. AIDevOpsAgentAccessPolicy is attached BY ARN,
# never inlined: it is AWS-owned and AWS-revised.
#
# NOTE the absence of `permissions_boundary`. Constraint 2: this role must be
# able to describe/read across every service an investigation touches.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "agentspace" {
  name               = local.agentspace_role_name
  description        = "AWS DevOps Agent Agent Space role (a1 shape) for the ${var.agentspace_name} Agent Space. Long-lived; not a scenario harness role."
  assume_role_policy = local.a1_trust_policy

  lifecycle {
    precondition {
      condition     = local.account_id == var.account_id
      error_message = "Refusing to apply: caller account ${local.account_id} != var.account_id ${var.account_id}. Check your AWS profile."
    }
  }
}

resource "aws_iam_role_policy_attachment" "agentspace_agent_access" {
  role       = aws_iam_role.agentspace.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AIDevOpsAgentAccessPolicy"
}

# The one grant AIDevOpsAgentAccessPolicy does not carry (a1).
resource "aws_iam_role_policy" "agentspace_resource_explorer_slr" {
  name   = "resource-explorer-slr"
  role   = aws_iam_role.agentspace.id
  policy = local.a1_slr_policy
}

# ---------------------------------------------------------------------------
# Operator Web App role — a4 shape.
# Trust policy verbatim: it allows BOTH sts:AssumeRole and sts:TagSession,
# because the Operator App policy scopes every statement by the
# ${aws:PrincipalTag/AgentSpaceId} session tag the service sets at assume time.
# Drop sts:TagSession and Web App login fails.
#
# Plus b5's caller grants: the Operator Web App tier touches CMK-encrypted Agent
# Space data (chat, assets, knowledge items), so it needs the same five KMS
# actions behind the kms:ViaService fence.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "operator_app" {
  name               = local.operator_app_role_name
  description        = "AWS DevOps Agent Operator Web App role (a4 shape): AIDevOpsOperatorAppAccessPolicy + b5 CMK caller grants."
  assume_role_policy = local.a4_trust_policy
}

resource "aws_iam_role_policy_attachment" "operator_app_access" {
  role       = aws_iam_role.operator_app.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy"
}

resource "aws_iam_role_policy" "operator_app_kms_caller" {
  name   = "b5-cmk-caller"
  role   = aws_iam_role.operator_app.id
  policy = local.b5_caller_policy
}

# ---------------------------------------------------------------------------
# Installer role — b8 shape (b2 + b5 caller + b7 CloudWatch-Logs target),
# trusted by the APPLYING OPERATOR'S OWN PRINCIPAL.
#
# This is the point of the deployment: the Agent Space is created by the
# least-privilege installer identity, not by Admin. After apply:
#
#   aws sts assume-role --role-arn <installer_role_arn> --role-session-name install
#
# then run the CLI runbook with those credentials. If a documented step fails
# with AccessDenied, that is a real finding about the b2/b5/b7 artifacts — fix
# the scenario, never widen this role in place.
#
# Each source artifact stays a SEPARATE inline policy: merging them would lose
# the provenance and risk the 10,240-character per-policy limit.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "installer" {
  name        = local.installer_role_name
  description = "Least-privilege installer identity (b2 + b5 + b7) assumed by the operator to run the Agent Space CLI runbook."
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowApplyingOperatorToAssume"
      Effect    = "Allow"
      Principal = { AWS = var.operator_principal_arn }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "installer_b2" {
  name   = "b2-installer"
  role   = aws_iam_role.installer.id
  policy = local.b2_installer_policy
}

resource "aws_iam_role_policy" "installer_b5_caller" {
  name   = "b5-cmk-caller"
  role   = aws_iam_role.installer.id
  policy = local.b5_caller_policy
}

resource "aws_iam_role_policy" "installer_b7_logs" {
  name   = "b7-cloudwatch-logs-target"
  role   = aws_iam_role.installer.id
  policy = local.b7_logs_target_policy
}
