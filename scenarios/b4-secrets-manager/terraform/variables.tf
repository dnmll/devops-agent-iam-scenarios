variable "region" {
  type    = string
  default = "us-east-1"
}

variable "ci_role_arn" {
  description = "ARN of iamscn-ci-role; the roles under test trust it so probes can assume in"
  type        = string
}

variable "boundary_arn" {
  description = "ARN of the iamscn-boundary permissions boundary (mandatory on all created roles)"
  type        = string
}

variable "run_id" {
  description = "GitHub Actions run id, used for tag-scoped cleanup"
  type        = string
  default     = "local"
}
