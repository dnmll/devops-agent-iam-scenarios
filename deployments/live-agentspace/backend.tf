terraform {
  backend "s3" {
    # Own state key — never shared with terraform/bootstrap/ or any scenario
    # harness, so a `terraform destroy` in one can never touch this deployment.
    # bucket/region are supplied by the operator with -backend-config
    # (see README.md); this module is never applied by CI.
    key = "deployments/live-agentspace/terraform.tfstate"
  }
}
