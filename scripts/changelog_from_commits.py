#!/usr/bin/env python3
"""Suggest Keep-a-Changelog bullets from Conventional Commits since a ref.

Usage:
  python3 scripts/changelog_from_commits.py
  python3 scripts/changelog_from_commits.py --since v1.1.0
  python3 scripts/changelog_from_commits.py --since HEAD~20
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict

TYPE_MAP = {
    "feat": "Added",
    "fix": "Fixed",
    "docs": "Changed",
    "style": "Changed",
    "refactor": "Changed",
    "perf": "Changed",
    "test": "Changed",
    "build": "Changed",
    "ci": "Changed",
    "chore": "Changed",
    "revert": "Changed",
    "security": "Security",
    "deprecate": "Deprecated",
    "remove": "Removed",
}

COMMIT_RE = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|security|deprecate|remove)"
    r"(?:\((?P<scope>[^)]+)\))?!?:\s*(?P<sub>.+)$",
    re.IGNORECASE,
)


def git_log(since: str | None) -> list[str]:
    cmd = ["git", "log", "--pretty=format:%s"]
    if since:
        cmd.append(f"{since}..HEAD")
    else:
        cmd.append("-50")
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def classify(subject: str) -> tuple[str, str] | None:
    m = COMMIT_RE.match(subject)
    if not m:
        return None
    section = TYPE_MAP.get(m.group("type").lower(), "Changed")
    scope = m.group("scope")
    body = m.group("sub").strip()
    if scope:
        body = f"**{scope}:** {body}"
    # Capitalize first letter for changelog style
    if body and body[0].islower():
        body = body[0].upper() + body[1:]
    return section, body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=None, help="Git ref (tag/commit); default last 50 commits")
    args = ap.parse_args()
    subjects = git_log(args.since)
    if not subjects:
        print("Keine Commits gefunden (oder kein Git-Repo).", file=sys.stderr)
        print("\n_Tipp: Ohne Conventional Commits manuell in CHANGELOG.md unter [Unreleased] pflegen._\n")
        return 0

    buckets: dict[str, list[str]] = defaultdict(list)
    skipped = 0
    for subj in subjects:
        hit = classify(subj)
        if not hit:
            skipped += 1
            continue
        section, line = hit
        if line not in buckets[section]:
            buckets[section].append(line)

    order = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]
    print("## [Unreleased] — Vorschlag aus Conventional Commits\n")
    if args.since:
        print(f"_Seit `{args.since}` · {len(subjects)} Commit(s), {skipped} ohne Conventional-Prefix_\n")
    for section in order:
        items = buckets.get(section) or []
        if not items:
            continue
        print(f"### {section}\n")
        for item in items:
            print(f"- {item}")
        print()
    if not any(buckets.values()):
        print("_Keine Conventional Commits erkannt. Beispiel: `feat(auth): session cookies`._\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
