output "slr_role_arn" {
  description = "Probe anchor carrying policies/slr-inline-policy.json; probes.yaml role_under_test points here"
  value       = module.slr_role.role_arn
}

output "secondary_role_arn" {
  description = "The deliverable: secondary-account role created with the cross-account trust policy verbatim (aws:SourceAccount = monitoring account) + AIDevOpsAgentAccessPolicy + inline SLR policy"
  value       = aws_iam_role.secondary.arn
}
