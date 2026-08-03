"""Parse Keep a Changelog markdown into structured entries for the API/UI."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_PATH = _ROOT / "CHANGELOG.md"

_VERSION_RE = re.compile(
    r"^##\s+\[([^\]]+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?\s*$",
    re.MULTILINE,
)
_SECTION_RE = re.compile(r"^###\s+(\w+)\s*$", re.MULTILINE)


def parse_changelog(path: Path | None = None) -> list[dict[str, Any]]:
    """Return newest-first list of {version, date, sections: {Added: [..], ...}}."""
    p = path or CHANGELOG_PATH
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8")
    matches = list(_VERSION_RE.finditer(text))
    if not matches:
        return []

    releases: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        sections: dict[str, list[str]] = {}
        sec_matches = list(_SECTION_RE.finditer(body))
        for j, sm in enumerate(sec_matches):
            name = sm.group(1)
            s0 = sm.end()
            s1 = sec_matches[j + 1].start() if j + 1 < len(sec_matches) else len(body)
            bullets = []
            for line in body[s0:s1].splitlines():
                line = line.strip()
                if line.startswith("- "):
                    bullets.append(line[2:].strip())
            if bullets:
                sections[name] = bullets
        releases.append(
            {
                "version": m.group(1),
                "date": m.group(2) or None,
                "sections": sections,
            }
        )
    return releases


def changelog_flat_entries(path: Path | None = None) -> list[dict[str, Any]]:
    """Flatten releases into card-style entries (compat with older UI)."""
    out: list[dict[str, Any]] = []
    for rel in parse_changelog(path):
        ver = rel["version"]
        date = rel.get("date") or ""
        for section, items in (rel.get("sections") or {}).items():
            for item in items:
                out.append(
                    {
                        "date": date or ver,
                        "title": f"[{ver}] {section}: {item[:80]}",
                        "body": item,
                        "by": "Lynx",
                        "version": ver,
                        "section": section,
                    }
                )
    return out
