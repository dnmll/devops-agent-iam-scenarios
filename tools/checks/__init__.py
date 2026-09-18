"""Static validation suite for IAM scenario artifacts.

Every check takes a Scenario and returns a list of Finding. The suite is
data-driven: adding a scenario must never require touching this package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

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

    def artifact_paths(self) -> list[Path]:
        return [self.path / a for a in self.manifest.get("artifacts", [])]

    def load_policies(self) -> dict[Path, dict]:
        out = {}
        for p in self.artifact_paths():
            if p.is_file():
                out[p] = json.loads(p.read_text())
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
