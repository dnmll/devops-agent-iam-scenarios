terraform {
  backend "s3" {
    # bucket/region supplied via -backend-config in CI (see live-validate.yml)
    key = "scenarios/b6-cicd-deployer/terraform.tfstate"
  }
}
