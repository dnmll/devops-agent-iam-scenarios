# Decisions log

## D1 — Terraform provisions IAM primitives only; probes exercise `aidevops`
The awscc/Cloud Control coverage for `aidevops` resource types is unverified, and
real API probes are higher-fidelity validation of a policy than resource creation
via a provider anyway. Scenario harnesses use the plain `hashicorp/aws` provider
for roles/policies; boto3 probes (with the assumed role-under-test) call the
DevOps Agent service directly. Revisit if/when `awscc_devopsagent_*` resources
are needed for a scenario (b6 will test exactly that).

## D2 — Raw policy JSON uses docs-style placeholders, substituted by the harness
Customer deliverables read like AWS documentation (`111122223333`, `us-east-1`,
`DevOpsAgentRole-*`). `scenario.yaml` `substitutions` maps them to sandbox
values (`iamscn-b2-dar-*`, real account/region) in terraform and the probe
runner. Trade-off: the tested policy differs from the shipped one only by these
literal substitutions.

## D3 — Parliament UNKNOWN_ACTION downgraded for the `aidevops` prefix
The linter's action database doesn't know the new service. Manifest-driven
custom checks (`required_conditions`, `forbidden_actions`) own correctness for
that prefix; parliament still gates everything else at full strength.

## D4 — boto3 may lack the `aidevops` service model
Real probes report `unverifiable` (and fail the run) rather than silently
skipping. Fix is upgrading boto3 in `tools/requirements.txt`; until the SDK
ships the model, real aidevops probes can be temporarily downgraded to
simulate-only in `probes.yaml` — never removed.
