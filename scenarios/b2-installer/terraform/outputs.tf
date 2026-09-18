output "installer_role_arn" {
  description = "Role under test; probes.yaml role_under_test points here"
  value       = module.installer_role.role_arn
}

output "agentspace_target_role_arn" {
  description = "Passable DevOpsAgentRole-prefixed target for real PassRole probes"
  value       = aws_iam_role.agentspace_target.arn
}
