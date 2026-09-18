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
      "iamscn:scenario" = "b7-log-delivery"
      "iamscn:run-id"   = var.run_id
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # One role per destination: each artifact is an independent deliverable, and
  # the cross-target deny probes only mean something if the destinations are
  # never attached together.
  targets = {
    cloudwatch = "cloudwatch-logs-target.json"
    s3         = "s3-target.json"
    firehose   = "firehose-target.json"
  }

  # Apply the scenario.yaml substitutions to the raw customer artifacts: account
  # id, region, and the destination names (the harness keeps everything it names
  # inside the iamscn- namespace).
  policy_json = {
    for target, policy_file in local.targets : target => replace(
      replace(
        replace(
          replace(
            file("${path.module}/../policies/${policy_file}"),
            "111122223333", data.aws_caller_identity.current.account_id
          ),
          "us-east-1", var.region
        ),
        "devops-agent-vended-logs", "iamscn-b7-vended-logs"
      ),
      "/aws/vendedlogs/devops-agent/", "/aws/vendedlogs/iamscn-b7/"
    )
  }
}

# IAM primitives only: the delivery sources, destinations, log groups, buckets
# and Firehose streams these policies name are never created here — the probes
# are all `kind: simulate`, so nothing outside IAM needs to exist. See
# expected/probes.yaml for why there are no `real` probes.
module "target_role" {
  source           = "../../../terraform/modules/scenario-role"
  for_each         = local.targets
  name             = "iamscn-b7-${each.key}"
  scenario         = "b7-log-delivery"
  assume_role_arns = [var.ci_role_arn]
  policy_json      = local.policy_json[each.key]
  boundary_arn     = var.boundary_arn
}
