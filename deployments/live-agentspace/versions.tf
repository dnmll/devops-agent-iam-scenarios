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
    # Deliberately NOT the iamscn:* namespace. Every tag-scoped cleanup in this
    # repo (live-validate's destroy sweep, .github/workflows/sweeper.yml) selects
    # on iamscn:scenario / iamscn:run-id; a long-lived deployment must be
    # un-selectable by construction, not by an exemption list.
    tags = {
      "devops-agent:deployment" = "live-agentspace"
      "devops-agent:managed-by" = "terraform"
      "devops-agent:lifecycle"  = "long-lived"
    }
  }
}
