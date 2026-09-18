resource "aws_s3_bucket" "tfstate" {
  bucket = "iamscn-tfstate-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "ci_role_arn" {
  value = aws_iam_role.ci.arn
}

output "boundary_arn" {
  value = aws_iam_policy.boundary.arn
}

output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}
