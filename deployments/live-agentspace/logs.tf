# ---------------------------------------------------------------------------
# Vended-log delivery destination (b7, CloudWatch Logs target).
#
# The log group must exist before `logs:PutDeliveryDestination` names it, and the
# /aws/vendedlogs/ prefix is AWS's convention for vended logs — it is also the
# prefix b7's logs:CreateLogGroup grant is scoped to, so the installer role could
# create this itself; creating it here instead means the destination survives
# between sessions like everything else in this module.
#
# Deliberately NOT encrypted with the CMK above: a CMK-encrypted log group needs
# its own logs.<region>.amazonaws.com key-policy statement, which is a different
# grant from the aidevops ones (see b7's SSE-KMS caveat). Default CloudWatch Logs
# encryption applies.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "vended_logs" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days == 0 ? null : var.log_retention_days
}

# ---------------------------------------------------------------------------
# Delivery IS performed by the AWS log-delivery service, not by your identity,
# so the destination side must allow that service principal to write. For a
# CloudWatch Logs destination that authorization is an ACCOUNT-LEVEL CloudWatch
# Logs resource policy (there is no per-log-group policy API).
#
# CAUTION (b7 spells this out): these documents are account-wide. A policy_name
# collision REPLACES the existing document and can revoke other services'
# log-delivery grants. This resource uses its own dedicated name, and the whole
# thing can be turned off with manage_delivery_resource_policy = false if your
# account already manages its delivery grants centrally — read the current
# documents (`aws logs describe-resource-policies`) before you apply.
#
# The aws:SourceAccount + aws:SourceArn pair is mandatory, not decoration:
# without it the statement is a confused-deputy hole that lets any account's log
# delivery write into this log group.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "delivery" {
  statement {
    sid    = "AWSLogDeliveryWriteForDevOpsAgent"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.vended_logs.arn}:log-stream:*"]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:logs:${var.region}:${local.account_id}:*"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "delivery" {
  count           = var.manage_delivery_resource_policy ? 1 : 0
  policy_name     = "DevOpsAgentVendedLogDelivery-${var.agentspace_name}"
  policy_document = data.aws_iam_policy_document.delivery.json
}
