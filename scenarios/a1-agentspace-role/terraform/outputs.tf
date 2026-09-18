output "slr_role_arn" {
  description = "Probe anchor carrying policies/slr-inline-policy.json; probes.yaml role_under_test points here"
  value       = module.slr_role.role_arn
}

output "agentspace_role_arn" {
  description = "The deliverable: Agent Space role created with the trust policy verbatim + AIDevOpsAgentAccessPolicy + inline SLR policy"
  value       = aws_iam_role.agentspace.arn
}
