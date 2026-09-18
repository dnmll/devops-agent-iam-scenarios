output "deployer_role_arn" {
  description = "CI/CD deployer role (the pipeline identity); probes.yaml role_under_test points here"
  value       = module.deployer_role.role_arn
}

output "passable_role_arn" {
  description = "Pre-existing iamscn-b6-dar-agentspace role — the only role the deployer may pass"
  value       = aws_iam_role.passable_agentspace_role.arn
}
