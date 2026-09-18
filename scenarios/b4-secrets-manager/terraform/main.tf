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
      "iamscn:scenario" = "b4-secrets-manager"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Apply the scenario.yaml substitutions to the raw customer artifact: account
  # id, region, and the secret name prefix (the harness keeps everything it
  # touches inside the iamscn- namespace).
  policy_json = replace(
    replace(
      replace(
        file("${path.module}/../policies/integration-registrar-policy.json"),
        "111122223333", data.aws_caller_identity.current.account_id
      ),
      "us-east-1", var.region
    ),
    "devops-agent/", "iamscn-b4/"
  )
}

# The identity under test: assumable by the CI role, capped by the boundary.
module "registrar_role" {
  source           = "../../../terraform/modules/scenario-role"
  name             = "iamscn-b4-registrar"
  scenario         = "b4-secrets-manager"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.policy_json
  boundary_arn     = var.boundary_arn
}
