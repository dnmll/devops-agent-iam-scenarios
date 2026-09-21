# ---------------------------------------------------------------------------
# Customer-managed key for Agent Space data at rest.
#
# Requirements from the encryption-at-rest docs (see b5): symmetric,
# SYMMETRIC_DEFAULT, ENCRYPT_DECRYPT. Multi-Region and asymmetric keys are NOT
# supported. The key ARN (not an alias, not a key id) is what
# `create-agent-space --kms-key-arn` takes, and it can only be set at CREATION
# time — you cannot add or change the CMK on an existing Agent Space.
# ---------------------------------------------------------------------------
resource "aws_kms_key" "agentspace" {
  description              = "AWS DevOps Agent Agent Space (${var.agentspace_name}) data at rest"
  key_usage                = "ENCRYPT_DECRYPT"
  customer_master_key_spec = "SYMMETRIC_DEFAULT"
  multi_region             = false
  enable_key_rotation      = true
  deletion_window_in_days  = var.kms_deletion_window_days
  policy                   = data.aws_iam_policy_document.key_policy.json
}

resource "aws_kms_alias" "agentspace" {
  name          = "alias/${var.kms_alias_name}"
  target_key_id = aws_kms_key.agentspace.key_id
}

# ---------------------------------------------------------------------------
# Key policy = b5's policies/key-policy.json PLUS the administration statement
# b5 inherited-as-missing from the AWS docs example.
#
# CONSTRAINT 3. The documented example mentions AllowKeyAdministration in prose
# and then omits it from the JSON. Harmless in a static artifact; on a real key
# it is a one-way door — a key policy with no administration statement can never
# be modified again (KMS authorizes kms:PutKeyPolicy from the key policy itself;
# IAM alone cannot recover it), and rotation/deletion/tagging are equally locked
# out. b5 documents key administration as "deliberately absent from the
# deliverable, belonging to the key owner's existing statement" — this is that
# statement, and a real deployment must not ship without it.
#
# Resource "*" in a key policy means "this key": a key policy is attached to
# exactly one key and KMS accepts no other form. Scoping is done by Principal +
# conditions.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "key_policy" {
  # 1. Administration. Account root keeps the key editable; IAM policies in this
  # account can then delegate administration normally. Optional named admin
  # roles are added alongside, never instead: a policy naming only a role is one
  # deleted role away from an unmanageable key.
  statement {
    sid    = "AllowKeyAdministration"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = concat(["arn:${local.partition}:iam::${local.account_id}:root"], var.key_administrator_arns)
    }
    actions = [
      "kms:Create*",
      "kms:Describe*",
      "kms:Enable*",
      "kms:List*",
      "kms:Put*",
      "kms:Update*",
      "kms:Revoke*",
      "kms:Disable*",
      "kms:Get*",
      "kms:Delete*",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:ScheduleKeyDeletion",
      "kms:CancelKeyDeletion",
      "kms:RotateKeyOnDemand",
    ]
    resources = ["*"]
  }

  # 2. The caller half of the two-sided grant, for BOTH caller identities: the
  # installer (validates the key and encrypts at creation time) and the Operator
  # Web App role (synchronous work inside the Agent Space). kms:ViaService means
  # neither principal can use this key outside the DevOps Agent path — a
  # human-typed kms:Decrypt carries no kms:ViaService value at all, so the
  # condition does not match. The five actions are exactly b5's documented set.
  statement {
    sid    = "AllowCallerAccessViaService"
    effect = "Allow"
    principals {
      type = "AWS"
      identifiers = [
        aws_iam_role.installer.arn,
        aws_iam_role.operator_app.arn,
      ]
    }
    actions = [
      "kms:DescribeKey",
      "kms:GenerateDataKey*",
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:ReEncrypt*",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = [local.aidevops_via_service]
    }
  }

  # 3. Configuration-time key validation by the service itself. Intentionally
  # unconditioned: at validation time no Agent Space ARN exists yet, so there is
  # nothing for aws:SourceArn or the encryption context to match — and
  # DescribeKey returns key metadata only.
  statement {
    sid    = "AllowDevOpsAgentServiceDescribeKeyAccess"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["aidevops.amazonaws.com"]
    }
    actions   = ["kms:DescribeKey"]
    resources = ["*"]
  }

  # 4 and 5. Asynchronous crypto by the service principal — investigations,
  # incident analysis, RCA generation. Miss these and Agent Space creation still
  # succeeds, then every investigation silently fails.
  #
  # ONE STATEMENT PER RESOURCE TYPE, on purpose (b5): collapsing agentspace/* and
  # service/* into a single statement with a list would let aws:SourceArn and the
  # encryption context be satisfied INDEPENDENTLY, so a request sourced from an
  # Agent Space could carry a *service* encryption context. Separate statements
  # keep the two keys correlated per resource type.
  dynamic "statement" {
    for_each = {
      AllowDevOpsAgentAccessForAgentSpace = local.agentspace_arn_wildcard
      AllowDevOpsAgentAccessForService    = local.service_arn_wildcard
    }
    content {
      sid    = statement.key
      effect = "Allow"
      principals {
        type        = "Service"
        identifiers = ["aidevops.amazonaws.com"]
      }
      actions = [
        "kms:GenerateDataKey*",
        "kms:Decrypt",
        "kms:Encrypt",
        "kms:ReEncrypt*",
      ]
      resources = ["*"]
      condition {
        test     = "ArnLike"
        variable = "aws:SourceArn"
        values   = [statement.value]
      }
      condition {
        test     = "StringLike"
        variable = "kms:EncryptionContext:aws-crypto-ec:aws:aidevops:arn"
        values   = [statement.value]
      }
    }
  }
}
