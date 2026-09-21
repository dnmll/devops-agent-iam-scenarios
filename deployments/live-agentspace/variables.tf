variable "region" {
  description = "Region the Agent Space lives in. Must match the Region used in the CLI runbook: kms:ViaService and the aidevops ARNs below are Region-pinned."
  type        = string
  default     = "us-east-1"
}

variable "account_id" {
  description = "Account id this deployment is applied into. Checked against the caller identity at plan time so a mis-targeted profile fails before anything is created."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account id."
  }
}

variable "operator_principal_arn" {
  description = <<-EOT
    The human operator's own principal (e.g.
    arn:aws:iam::111122223333:role/AWSReservedSSO_AdministratorAccess_abc123 or
    arn:aws:iam::111122223333:user/alice). Trusted by the installer role ONLY —
    the point of the exercise is that the operator assumes the least-privilege
    installer role and runs the CLI as that identity, rather than creating the
    Agent Space with their own admin credentials.
  EOT
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:(iam|sts)::", var.operator_principal_arn))
    error_message = "operator_principal_arn must be an IAM/STS principal ARN (role, user, or assumed-role)."
  }
}

variable "name_prefix" {
  description = <<-EOT
    Prefix for every IAM role this module creates. MUST NOT be `iamscn-`:
    tools/probes/sweeper.py deletes iamscn-* roles and Agent Spaces older than
    six hours, which would destroy this deployment overnight. The default is the
    AWS-documented `DevOpsAgentRole-` convention, which is also what b2's
    iam:PassRole statement is scoped to (role/DevOpsAgentRole-*), so the
    installer role can pass these roles to the service without widening b2.
  EOT
  type        = string
  default     = "DevOpsAgentRole-"

  validation {
    condition     = !startswith(var.name_prefix, "iamscn-")
    error_message = "name_prefix must not start with iamscn- : tools/probes/sweeper.py deletes aged iamscn-* roles and Agent Spaces."
  }

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9+=,.@_-]*$", var.name_prefix))
    error_message = "name_prefix must be a valid IAM name fragment."
  }
}

variable "agentspace_name" {
  description = "Name the operator will give the Agent Space with `aws devops-agent create-agent-space`. Used only for the log-group path and for documentation; this module creates no aidevops resources (decision D1)."
  type        = string
  default     = "live"

  validation {
    condition     = !startswith(var.agentspace_name, "iamscn-")
    error_message = "agentspace_name must not start with iamscn- : the sweeper deletes aged iamscn-* Agent Spaces."
  }
}

variable "kms_alias_name" {
  description = "Alias for the customer-managed key, without the `alias/` prefix."
  type        = string
  default     = "devops-agent/live-agentspace"
}

variable "kms_deletion_window_days" {
  description = "Waiting period if the key is ever scheduled for deletion. KMS minimum is 7; a longer window is the safer default for a key that protects real Agent Space data (deleting it is permanent data loss — DevOps Agent does not re-encrypt under a new key)."
  type        = number
  default     = 30

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "kms_deletion_window_days must be between 7 and 30."
  }
}

variable "key_administrator_arns" {
  description = "Extra principals (besides the account root) that may administer the CMK's policy, grants and rotation. Empty by default: the account root statement alone is enough to keep the key policy editable."
  type        = list(string)
  default     = []
}

variable "manage_delivery_resource_policy" {
  description = "Create the account-level CloudWatch Logs resource policy that lets delivery.logs.amazonaws.com write into the destination log group. Set false if your account manages those documents centrally — they are account-wide and a name collision replaces an existing one (see logs.tf and b7)."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Retention on the vended-log destination log group. 0 keeps logs forever."
  type        = number
  default     = 90
}
