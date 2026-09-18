---
name: Scenario task (ABCA)
about: A bounded task for an autonomous background coding agent
title: "[scenario-id] short imperative description"
labels: []
---

## Task

<!-- One bounded change. Reference the scenario directory and the exact files to touch.
     Remind the agent: data files only — scenario.yaml / policies/*.json / probes.yaml /
     README.md / matrix row. No changes to tools/checks, workflows, or bootstrap. -->

## Context

<!-- Which scenario, which AWS doc pages ground the actions (paste URLs), any
     conventions from .claude/rules/ that matter here. -->

## Acceptance criteria

<!-- These are the agent's definition of done — be explicit and machine-checkable: -->

- [ ] `python3 -m tools.checks` passes
- [ ] `python3 -m pytest tools/checks/tests -q` passes
- [ ] Every new IAM action is traceable to a doc URL listed in `scenario.yaml` `docs:`
- [ ] `docs/scenario-matrix.md` row updated (status column matches `scenario.yaml`)
- [ ] No files changed outside `scenarios/<id>/` and `docs/scenario-matrix.md`
