output "registrar_role_arn" {
  description = "Integration registrar role; probes.yaml role_under_test points here"
  value       = module.registrar_role.role_arn
}
