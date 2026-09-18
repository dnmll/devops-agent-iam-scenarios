output "preprovisioner_role_arn" {
  description = "Role under test; probes.yaml role_under_test points here"
  value       = module.preprovisioner_role.role_arn
}

output "preprovisioned_target_role_arn" {
  description = "Existing DevOpsAgentRole-prefixed role the lifecycle probes target"
  value       = aws_iam_role.preprovisioned_target.arn
}
