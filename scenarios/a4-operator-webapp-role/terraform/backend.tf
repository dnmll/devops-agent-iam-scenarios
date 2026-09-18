terraform {
  backend "s3" {
    # bucket/region supplied via -backend-config in CI (see live-validate.yml)
    key = "scenarios/a4-operator-webapp-role/terraform.tfstate"
  }
}
