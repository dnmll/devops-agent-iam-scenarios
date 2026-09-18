terraform {
  backend "s3" {
    # bucket/region supplied via -backend-config in CI (see live-validate.yml)
    key = "scenarios/b3-webapp-tiers/terraform.tfstate"
  }
}
