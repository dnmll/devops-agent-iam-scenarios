# Permissions boundary attached to EVERY scenario-created role. Caps what any
# candidate policy under test can actually do, regardless of how broad it is.
resource "aws_iam_policy" "boundary" {
  name        = "iamscn-boundary"
  description = "Cap for all iamscn-* scenario roles (union of legitimate scenario needs)"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DevOpsAgentSurface"
        Effect   = "Allow"
        Action   = ["aidevops:*"]
        Resource = "*"
      },
      {
        Sid      = "ScopedIamReads"
        Effect   = "Allow"
        Action   = ["iam:GetRole", "iam:ListRoles"]
        Resource = "*"
      },
      {
        Sid      = "PassScenarioRolesOnly"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/iamscn-*"
      },
      {
        Sid      = "KnownServiceLinkedRolesOnly"
        Effect   = "Allow"
        Action   = ["iam:CreateServiceLinkedRole"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "iam:AWSServiceName" = [
              "aidevops.amazonaws.com",
              "resource-explorer-2.amazonaws.com",
              "delivery.logs.amazonaws.com"
            ]
          }
        }
      },
      # Later milestones (B4/B5/B7) widen here: scoped kms, secretsmanager, logs,
      # firehose, s3 — union of scenario needs, never iam:* write.
      {
        Sid    = "DenyBoundaryTampering"
        Effect = "Deny"
        Action = [
          "iam:DeleteRolePermissionsBoundary",
          "iam:PutRolePermissionsBoundary",
          "iam:CreatePolicyVersion",
          "iam:DeletePolicy"
        ]
        Resource = "*"
      }
    ]
  })
}
