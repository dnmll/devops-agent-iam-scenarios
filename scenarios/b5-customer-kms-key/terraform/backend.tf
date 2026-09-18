terraform {
  backend "s3" {
    # bucket/region supplied via -backend-config in CI (see live-validate.yml)
    key = "scenarios/b5-customer-kms-key/terraform.tfstate"
  }
}
