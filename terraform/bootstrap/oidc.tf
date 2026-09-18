resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "ci" {
  name = "iamscn-ci-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          # Pinned to the protected environment: live runs always pass the human gate.
          # Repos with GitHub's immutable OIDC subject enabled (default for new
          # repos; check GET /repos/{o}/{r}/actions/oidc/customization/sub) send
          # owner@id/repo@id in the sub claim — set github_repo_immutable to the
          # returned sub_claim_prefix (minus the "repo:" prefix) for those.
          "token.actions.githubusercontent.com:sub" = "repo:${coalesce(var.github_repo_immutable, var.github_repo)}:environment:${var.environment_name}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "ci" {
  name = "iamscn-ci-permissions"
  role = aws_iam_role.ci.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ScenarioRoleCrudWithBoundary"
        Effect   = "Allow"
        Action   = ["iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy"]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/iamscn-*"
        Condition = {
          StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.boundary.arn }
        }
      },
      {
        Sid    = "ScenarioRoleLifecycle"
        Effect = "Allow"
        Action = [
          "iam:GetRole", "iam:DeleteRole", "iam:TagRole", "iam:UntagRole",
          "iam:ListRolePolicies", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
          "iam:ListAttachedRolePolicies", "iam:DetachRolePolicy",
          "iam:ListInstanceProfilesForRole", "iam:UpdateAssumeRolePolicy",
          "iam:SimulatePrincipalPolicy"
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/iamscn-*"
      },
      {
        Sid      = "ListForSweeper"
        Effect   = "Allow"
        Action   = ["iam:ListRoles", "iam:ListPolicies", "iam:ListRoleTags"]
        Resource = "*"
      },
      {
        Sid      = "AssumeScenarioRolesForProbes"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/iamscn-*"
      },
      {
        Sid      = "DevOpsAgentForProbesAndSweeper"
        Effect   = "Allow"
        Action   = ["aidevops:*"]
        Resource = "*"
      },
      {
        Sid      = "TfState"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
      },
      {
        Sid    = "ProtectPlatform"
        Effect = "Deny"
        Action = ["iam:*"]
        Resource = [
          aws_iam_role.ci.arn,
          aws_iam_policy.boundary.arn
        ]
      }
    ]
  })
}
