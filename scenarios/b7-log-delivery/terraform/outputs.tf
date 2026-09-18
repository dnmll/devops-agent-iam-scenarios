output "cloudwatch_role_arn" {
  description = "CloudWatch Logs destination role; probes.yaml role_under_test points here"
  value       = module.target_role["cloudwatch"].role_arn
}

output "s3_role_arn" {
  description = "S3 destination role (probes-s3.yaml role_under_test)"
  value       = module.target_role["s3"].role_arn
}

output "firehose_role_arn" {
  description = "Firehose destination role (probes-firehose.yaml role_under_test)"
  value       = module.target_role["firehose"].role_arn
}
