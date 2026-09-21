# The CLI runbook consumes these verbatim — do not rename.

output "agentspace_role_arn" {
  description = "Agent Space role (a1 shape). Pass to `create-agent-space --role-arn`."
  value       = aws_iam_role.agentspace.arn
}

output "operator_app_role_arn" {
  description = "Operator Web App role (a4 shape, sts:TagSession + b5 CMK caller grants). Pass to the Operator App enablement call."
  value       = aws_iam_role.operator_app.arn
}

output "installer_role_arn" {
  description = "Least-privilege installer identity. `aws sts assume-role` into this before running the runbook — the deployment is performed BY this role, not by Admin."
  value       = aws_iam_role.installer.arn
}

output "kms_key_arn" {
  description = "Customer-managed key. Pass the FULL ARN to `create-agent-space --kms-key-arn` (alias/key id are rejected, and the CMK cannot be changed afterwards)."
  value       = aws_kms_key.agentspace.arn
}

output "log_group_arn" {
  description = "Vended-log destination log group ARN. Pass to `logs:PutDeliveryDestination`."
  value       = aws_cloudwatch_log_group.vended_logs.arn
}

output "log_group_name" {
  description = "Vended-log destination log group name."
  value       = aws_cloudwatch_log_group.vended_logs.name
}
