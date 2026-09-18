terraform {
  backend "s3" {
    # bucket/region supplied via -backend-config in CI (see live-validate.yml)
    key = "scenarios/b1-iam-preprovisioner/terraform.tfstate"
  }
}
