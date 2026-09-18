output "admin_role_arn" {
  description = "Administrator tier role (probes-admin.yaml role_under_test)"
  value       = module.tier_role["admin"].role_arn
}

output "operator_role_arn" {
  description = "Operator tier role; probes.yaml role_under_test points here"
  value       = module.tier_role["operator"].role_arn
}

output "readonly_role_arn" {
  description = "Read-only tier role (probes-readonly.yaml role_under_test)"
  value       = module.tier_role["readonly"].role_arn
}
