output "remediation_role_arn" {
  description = "Probe anchor carrying policies/example-remediation-policy.json; probes.yaml role_under_test points here"
  value       = module.remediation_role.role_arn
}

output "elevated_role_arn" {
  description = "The deliverable: elevated directed-actions role created with the trust policy verbatim + the example remediation policy inline"
  value       = aws_iam_role.elevated.arn
}
