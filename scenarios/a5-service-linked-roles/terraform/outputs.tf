output "slr_role_arn" {
  description = "Role carrying policies/create-slr-policy.json; probes.yaml role_under_test points here"
  value       = module.slr_role.role_arn
}
