"""Static validation suite for IAM scenario artifacts.

Every check takes a Scenario and returns a list of Finding. The suite is
data-driven: adding a scenario must never require touching this package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..artifacts import ArtifactRef, ArtifactRefError, resolve_artifact_ref

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "scenarios"
SCHEMA_DIR = SCENARIOS_DIR / "_schema"
MATRIX_DOC = REPO_ROOT / "docs" / "scenario-matrix.md"

# Default forbidden grants; scenario.yaml forbidden_actions extends this list.
ALWAYS_FORBIDDEN = ["*", "iam:*"]


@dataclass
class Finding:
    scenario: str
    check: str
    message: str

    def line(self) -> str:
        return f"{self.scenario}: {self.check}: FAIL — {self.message}"


@dataclass
class Scenario:
    path: Path
    manifest: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.manifest.get("id", self.path.name)

    @property
    def scenarios_dir(self) -> Path:
        """Root the artifact references resolve against.

        The parent of the scenario directory, not the module-level
        SCENARIOS_DIR: tests build a Scenario from a copy under tmp_path, and a
        copy's references must resolve inside the copy.
        """
        return self.path.parent

    def artifact_refs(self) -> tuple[list[ArtifactRef], list[tuple[str, str]]]:
        """Resolved artifact references, plus (ref, reason) pairs that would not resolve.

        Unresolvable references are returned rather than raised so `check_manifest`
        can report them as ordinary findings alongside everything else.
        """
        refs: list[ArtifactRef] = []
        errors: list[tuple[str, str]] = []
        for a in self.manifest.get("artifacts", []):
            try:
                refs.append(resolve_artifact_ref(a, self.path, self.scenarios_dir))
            except ArtifactRefError as e:
                errors.append((a, str(e)))
        return refs, errors

    def artifact_paths(self) -> list[Path]:
        """Filesystem paths of the resolvable artifacts (unresolvable ones are
        reported by check_manifest and skipped here)."""
        refs, _ = self.artifact_refs()
        return [r.path for r in refs]

    def load_policies(self) -> dict[str, dict]:
        """{artifact reference as written: parsed policy} for artifacts on disk.

        Keyed by the reference string, not by path: a cross-scenario reference has
        no path relative to this scenario's directory, and the reference is what a
        finding or a `required_conditions` rule names.
        """
        out = {}
        refs, _ = self.artifact_refs()
        for ref in refs:
            if ref.path.is_file():
                out[ref.ref] = json.loads(ref.path.read_text())
        return out


def discover_scenarios(root: Path = SCENARIOS_DIR) -> list[Scenario]:
    scenarios = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        manifest_path = d / "scenario.yaml"
        manifest = {}
        if manifest_path.is_file():
            manifest = yaml.safe_load(manifest_path.read_text()) or {}
        scenarios.append(Scenario(path=d, manifest=manifest))
    return scenarios


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text())
