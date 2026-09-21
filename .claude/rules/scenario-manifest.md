# scenario.yaml contract

Validated against `scenarios/_schema/scenario.schema.json`. Fields:

- `id` — directory name, pattern `[ab][0-9]+[a-z0-9-]*` (e.g. `b2-installer`)
- `plane` — `A` (service-assumed role) or `B` (human/CI identity)
- `title`, `description` — one-liners; the matrix doc row must exist for `id`
- `status` — `planned | draft | static | live` (live = passed a live-validate run)
- `docs` — list of AWS doc URLs the actions are traceable to
- `artifacts` — artifact references (see below); must exist
- `terraform_dir` — harness dir (default `terraform`), must exist for status ≥ static
- `substitutions` — map applied by the harness and probe runner to the raw JSON
  (e.g. `"111122223333": "<account-id>"`, `"DevOpsAgentRole-": "iamscn-b2-"`)
- `required_conditions` — list of `{artifact, action, condition_key, expected}` assertions;
  `artifact` is the reference exactly as `artifacts:` writes it
- `forbidden_actions` — actions no Allow statement may grant (default includes `iam:*`, `*`)
- `parliament_suppressions` — list of `{issue, reason}`; reason is mandatory
- `probes` — path to `expected/probes.yaml` (schema: `probes.schema.json`)

## Artifact references

An entry of `artifacts:` (or of a probes file's `simulate_artifacts:`) is a path
relative to the **scenario directory**, resolved by `tools/artifacts.py`:

- `policies/installer-policy.json` — the scenario's own artifact.
- `../b2-installer/policies/installer-policy.json` — **another scenario's**
  artifact. A composite scenario points at a parent's deliverable instead of
  holding a copy, so editing the parent can never leave the composite stale.

`check_manifest` enforces, for a cross-scenario reference: the referenced
scenario directory exists, its `scenario.yaml` has `status: live` (a composite may
only build on a deliverable that has actually passed live-validate), the
referenced file is listed in *that* scenario's own `artifacts:` (otherwise it is a
copy by another name — nothing keeps it in sync), and the file exists.

References must resolve inside `scenarios/<some-scenario>/`. An absolute path, a
`~`, a climb out of `scenarios/`, or a path into `scenarios/_schema/` is a
manifest error — the resolver never follows it.

Every check (`check_policy_json`, `check_required_conditions`,
`check_parliament`) reads a referenced artifact exactly as it reads a local one,
and reports it by the reference as written.

## probes.yaml

Top level: `{role_under_test, simulate_artifacts?, probes}`.

- `role_under_test` — terraform output name whose ARN the `real` probes assume.
  It selects **nothing** about simulation.
- `simulate_artifacts` — the artifact references the `simulate` probes evaluate as
  `PolicyInputList`. Omit it to simulate all of `artifacts` (a customer attaches a
  scenario's policies together). Declare it when a probes file targets one
  identity tier of a multi-tier scenario (b3, b7: simulating the union would grant
  the other tiers' actions and turn every deny expectation into a false pass), or
  to exclude a non-simulatable trust/resource policy (a1–a4, b5 —
  `iam:SimulateCustomPolicy` accepts identity policies only).

Probe entries: `{name, kind: simulate|real, action | operation, resource?, context?, params?, expect: allowed|implicitDeny|success|error:<Code>}`.
