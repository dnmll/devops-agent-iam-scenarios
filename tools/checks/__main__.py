"""Run all static checks. Exit 1 on any failure.

Usage: python3 -m tools.checks [scenario-id ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import REPO_ROOT, discover_scenarios
from .checks import SCENARIO_CHECKS, check_shared_modules

REPORT_PATH = REPO_ROOT / "checks-report.json"


def main(argv: list[str]) -> int:
    only = set(argv)
    scenarios = discover_scenarios()
    if only:
        scenarios = [s for s in scenarios if s.id in only]
        if not scenarios:
            print(f"no scenarios matched {sorted(only)}", file=sys.stderr)
            return 2

    findings = check_shared_modules()
    results = []
    for s in scenarios:
        if s.manifest.get("status", "planned") == "planned":
            results.append((s.id, "SKIP (planned)"))
            continue
        before = len(findings)
        for check in SCENARIO_CHECKS:
            findings.extend(check(s))
        results.append((s.id, "OK" if len(findings) == before else "FAIL"))

    for sid, status in results:
        print(f"{sid:30s} {status}")
    for finding in findings:
        print(finding.line())

    REPORT_PATH.write_text(json.dumps({
        "scenarios": {sid: status for sid, status in results},
        "findings": [finding.line() for finding in findings],
    }, indent=2))

    if findings:
        print(f"\n{len(findings)} finding(s). See {REPORT_PATH.name}.")
        return 1
    print(f"\nAll checks passed ({len([r for r in results if r[1] == 'OK'])} scenario(s) checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
