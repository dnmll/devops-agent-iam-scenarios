# devops-agent-iam-scenarios

Validated IAM permission scenarios for [AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/): scenario-specific least-privilege policies that the public docs don't provide, each one machine-checked and live-validated.

- **Scenario catalog:** [docs/scenario-matrix.md](docs/scenario-matrix.md) — 11 scenarios across two planes (roles the service assumes; permissions humans/CI need)
- **Each scenario** (`scenarios/<id>/`): raw customer-usable policy JSON + README + a `scenario.yaml` contract that drives all validation
- **Static validation:** `python3 -m tools.checks` (policy shape, parliament lint, required condition keys, manifest integrity) — runs locally, in CI, and as the build gate for autonomous coding agents working this repo
- **Live validation:** GitHub Actions (human-gated `sandbox` environment) deploys the policy to a dedicated sandbox account, probes the real allow/deny behavior, and destroys everything — see [docs/sandbox-account.md](docs/sandbox-account.md)

## Quick start

```bash
python3 -m pip install -r tools/requirements.txt -r tools/requirements-dev.txt
python3 -m tools.checks
```

Scenario work is designed to be delegated to autonomous background coding agents (ABCA); see `CLAUDE.md` and `.github/ISSUE_TEMPLATE/scenario-task.md`.
