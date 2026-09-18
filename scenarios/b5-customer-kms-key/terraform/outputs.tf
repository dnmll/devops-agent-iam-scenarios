output "caller_role_arn" {
  description = "CMK caller role; probes.yaml role_under_test points here"
  value       = module.caller_role.role_arn
}
