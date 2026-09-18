#!/usr/bin/env python3
"""Delete iamscn-* scenario leftovers older than --max-age-hours.

Exits non-zero if anything had to be cleaned (a leftover means a destroy step
failed — the loud failure is the alert). Never touches iamscn-ci-role,
iamscn-boundary, service-linked roles, or the tfstate bucket.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

import boto3

PROTECTED = {"iamscn-ci-role"}


def sweep_iam_roles(max_age: timedelta) -> list[str]:
    iam = boto3.client("iam")
    cleaned = []
    cutoff = datetime.now(timezone.utc) - max_age
    for page in iam.get_paginator("list_roles").paginate(PathPrefix="/"):
        for role in page["Roles"]:
            name = role["RoleName"]
            if not name.startswith("iamscn-") or name in PROTECTED:
                continue
            if role["CreateDate"] > cutoff:
                continue
            for pol in iam.list_role_policies(RoleName=name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=name, PolicyName=pol)
            for att in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=name, PolicyArn=att["PolicyArn"])
            iam.delete_role(RoleName=name)
            cleaned.append(f"iam role {name}")
    return cleaned


def sweep_agent_spaces(max_age: timedelta) -> list[str]:
    cleaned = []
    try:
        client = boto3.client("aidevops")
    except Exception:
        print("note: boto3 lacks the aidevops model; skipping agent-space sweep")
        return cleaned
    cutoff = datetime.now(timezone.utc) - max_age
    spaces = client.list_agent_spaces().get("agentSpaces", [])
    for space in spaces:
        name = space.get("name", "")
        created = space.get("createdAt")
        if name.startswith("iamscn-") and created and created < cutoff:
            client.delete_agent_space(agentSpaceId=space["id"])
            cleaned.append(f"agent space {name}")
    return cleaned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-hours", type=float, default=6)
    args = ap.parse_args()
    max_age = timedelta(hours=args.max_age_hours)

    cleaned = sweep_iam_roles(max_age) + sweep_agent_spaces(max_age)
    if cleaned:
        print("LEFTOVERS FOUND AND CLEANED (a destroy step failed somewhere):")
        for item in cleaned:
            print(f"  - {item}")
        return 1
    print("clean — no aged iamscn resources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
