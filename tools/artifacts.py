"""Artifact reference resolution, shared by the checks and the probe runner.

An artifact reference is a path written in `scenario.yaml` (`artifacts:`) or in a
probes file (`simulate_artifacts:`), relative to the scenario directory:

    policies/installer-policy.json          # own artifact
    ../b2-installer/policies/installer-policy.json   # another scenario's

The cross-scenario form exists so a composite scenario can *point at* a parent
scenario's deliverable instead of holding a copy of it: single source of truth,
so editing the parent policy can never leave the composite stale.

Resolution is deliberately narrow — a reference must land inside the
`scenarios/` tree, inside some scenario's directory. Anything else (an absolute
path, a climb out of `scenarios/`, a path into `scenarios/` itself) is a
manifest error, not something to silently follow. This module only resolves and
classifies; the "referenced scenario exists / is `status: live`" policy lives in
`check_manifest`, which is where manifest findings are reported.

Zero third-party imports on purpose: the probe runner imports this in CI where
only boto3/yaml are installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPO_ROOT / "scenarios"


class ArtifactRefError(ValueError):
    """An artifact reference cannot be resolved inside the scenarios/ tree."""


@dataclass(frozen=True)
class ArtifactRef:
    """A resolved artifact reference.

    `ref` is the string as written (the label every finding and report uses —
    the reader should see what they typed, not an absolute path); `path` is where
    it landed; `scenario_dir` is the scenario that owns the file.
    """

    ref: str
    path: Path
    scenario_dir: Path
    owner_dir: Path

    @property
    def scenario_id(self) -> str:
        """Directory name of the scenario that owns the artifact."""
        return self.owner_dir.name

    @property
    def is_cross_scenario(self) -> bool:
        return self.owner_dir != self.scenario_dir


def resolve_artifact_ref(
    ref: str, scenario_dir: Path, scenarios_dir: Path = SCENARIOS_DIR
) -> ArtifactRef:
    """Resolve `ref` relative to `scenario_dir`; raise ArtifactRefError if it escapes.

    Normalisation is lexical (`os.path.normpath`), not `Path.resolve()`: the
    containment decision must be about the reference as written, and must not
    depend on whether the file happens to exist yet.
    """
    if not ref or not isinstance(ref, str):
        raise ArtifactRefError("artifact reference must be a non-empty string")
    if os.path.isabs(ref) or ref.startswith("~"):
        raise ArtifactRefError(f"'{ref}' is not relative to the scenario directory")

    scenario_dir = Path(os.path.normpath(scenario_dir))
    scenarios_dir = Path(os.path.normpath(scenarios_dir))
    target = Path(os.path.normpath(scenario_dir / ref))

    try:
        inside = target.relative_to(scenarios_dir)
    except ValueError:
        raise ArtifactRefError(
            f"'{ref}' resolves to {target} — outside the scenarios/ tree"
        ) from None
    if len(inside.parts) < 2:
        raise ArtifactRefError(
            f"'{ref}' resolves to {target} — not inside a scenario directory"
        )
    # `_`-prefixed directories under scenarios/ are infrastructure (`_schema/`),
    # not scenarios — discover_scenarios() skips them, so nothing keeps a
    # reference into one honest.
    if inside.parts[0].startswith("_"):
        raise ArtifactRefError(
            f"'{ref}' resolves to {target} — '{inside.parts[0]}' is not a scenario directory"
        )

    return ArtifactRef(
        ref=ref,
        path=target,
        scenario_dir=scenario_dir,
        owner_dir=scenarios_dir / inside.parts[0],
    )
