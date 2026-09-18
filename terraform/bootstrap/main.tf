# One-time sandbox-account bootstrap. Applied MANUALLY by an operator (never CI).
# Creates: GitHub OIDC provider, iamscn-ci-role, iamscn-boundary, tfstate bucket.

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
}

data "aws_caller_identity" "current" {}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo" {
  description = "owner/repo allowed to assume the CI role"
  type        = string
}

variable "environment_name" {
  type    = string
  default = "sandbox"
}

variable "github_repo_immutable" {
  description = "Immutable owner@account-id/repo@repo-id form of the repo, e.g. dnmll@91972154/devops-agent-iam-scenarios@1375390437; required when the repo uses immutable OIDC subject claims"
  type        = string
  default     = null
}
