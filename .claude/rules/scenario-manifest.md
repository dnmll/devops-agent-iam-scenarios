# scenario.yaml contract

Validated against `scenarios/_schema/scenario.schema.json`. Fields:

- `id` — directory name, pattern `[ab][0-9]+[a-z0-9-]*` (e.g. `b2-installer`)
- `plane` — `A` (service-assumed role) or `B` (human/CI identity)
- `title`, `description` — one-liners; the matrix doc row must exist for `id`
- `status` — `planned | draft | static | live` (live = passed a live-validate run)
- `docs` — list of AWS doc URLs the actions are traceable to
- `artifacts` — repo-relative paths of policy JSON files; must exist
- `terraform_dir` — harness dir (default `terraform`), must exist for status ≥ static
- `substitutions` — map applied by the harness and probe runner to the raw JSON
  (e.g. `"111122223333": "<account-id>"`, `"DevOpsAgentRole-": "iamscn-b2-"`)
- `required_conditions` — list of `{artifact, action, condition_key, expected}` assertions
- `forbidden_actions` — actions no Allow statement may grant (default includes `iam:*`, `*`)
- `parliament_suppressions` — list of `{issue, reason}`; reason is mandatory
- `probes` — path to `expected/probes.yaml` (schema: `probes.schema.json`)

`probes.yaml` entries: `{name, kind: simulate|real, action | operation, resource?, context?, params?, expect: allowed|implicitDeny|success|error:<Code>}`.
