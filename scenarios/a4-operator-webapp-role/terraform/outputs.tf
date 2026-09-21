output "operator_role_arn" {
  description = "Probe anchor carrying policies/operator-app-policy.json; probes.yaml role_under_test points here (probes.yaml names that artifact in simulate_artifacts)"
  value       = module.operator_role.role_arn
}

output "operator_app_role_arn" {
  description = "The deliverable: Operator Web App role created with the trust policy and the operator-app policy verbatim (policy variables intact)"
  value       = aws_iam_role.operator_app.arn
}
