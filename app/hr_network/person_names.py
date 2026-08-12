"""Person name identity: middle-name subset matching and display preference."""

from __future__ import annotations

import re
from typing import Any


def _slug_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower().strip()).strip("-")


def parse_person_name_parts(name: str) -> dict[str, Any]:
    """
    Split a person name into surname + given-name tokens.

    Accepts Swiss HR form «Nachname, Vorname …» and space-separated forms
    «Vorname … Nachname». Without a comma, the last token is treated as surname
    (western order); callers that know Last-First UI labels should keep the comma.
    """
    raw = (name or "").strip()
    if not raw:
        return {"raw": "", "last_name": "", "first_parts": []}

    if "," in raw:
        last, _, rest = raw.partition(",")
        last = last.strip()
        first_parts = [p for p in re.split(r"\s+", rest.strip()) if p]
    else:
        parts = [p for p in re.split(r"\s+", raw) if p]
        if len(parts) >= 2:
            last = parts[-1]
            first_parts = parts[:-1]
        elif parts:
            last = parts[0]
            first_parts = []
        else:
            last = ""
            first_parts = []

    return {
        "raw": raw,
        "last_name": last,
        "first_parts": first_parts,
    }


def _given_tokens(parts: dict[str, Any]) -> list[str]:
    return [t.lower().rstrip(".") for t in (parts.get("first_parts") or []) if t]


def _given_names_compatible(a: list[str], b: list[str]) -> bool:
    """
    True when first given names match and one given-name sequence is a
    prefix of the other (middle names only add detail).

    «Michael» ↔ «Michael Gabriel» → True
    «Michael Paul» ↔ «Michael Gabriel» → False (conflicting middles)
    «Michael» ↔ «Max» → False
    """
    if not a or not b:
        # Only surname (or missing given) — too weak to claim sameness alone.
        return False
    if a[0] != b[0]:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return long[: len(short)] == short


def names_same_person(a: str | None, b: str | None) -> bool:
    """
    Confident same-identity check for network collapse.

    Requires matching surname and compatible given names (first given equal;
    extra middle names only when one sequence is a prefix of the other).
    Does not merge different first names with the same surname.
    """
    if not a or not b:
        return False
    pa = parse_person_name_parts(a)
    pb = parse_person_name_parts(b)
    last_a = (pa.get("last_name") or "").strip().lower()
    last_b = (pb.get("last_name") or "").strip().lower()
    if not last_a or not last_b or last_a != last_b:
        return False
    return _given_names_compatible(_given_tokens(pa), _given_tokens(pb))


def person_identity_key(name: str | None) -> str:
    """
    Canonical fingerprint: surname + first given name (middles ignored).

    Used for secondary matching (case flags), not as the sole merge gate —
    conflicting middles still share this key but names_same_person rejects them.
    """
    parts = parse_person_name_parts(name or "")
    last = _slug_token(str(parts.get("last_name") or ""))
    firsts = _given_tokens(parts)
    first = _slug_token(firsts[0]) if firsts else ""
    if last and first:
        return f"{last}-{first}"
    return last or first or _slug_token(name or "")


def prefer_display_name(
    existing: str | None,
    candidate: str | None,
    *,
    prefer_existing: bool = False,
) -> str | None:
    """
    Pick the better label for a merged person.

    Prefer the more complete form (more given-name tokens / longer) when the
    names describe the same person. ``prefer_existing`` keeps the seed/first
    label when token counts are equal (stable seed display).
    """
    if not existing:
        return candidate
    if not candidate:
        return existing
    if not names_same_person(existing, candidate):
        return existing

    pe = parse_person_name_parts(existing)
    pc = parse_person_name_parts(candidate)
    ne = len(pe.get("first_parts") or [])
    nc = len(pc.get("first_parts") or [])
    if nc > ne:
        return candidate
    if ne > nc:
        return existing
    # Equal token depth: prefer Swiss HR comma form, then length, then seed.
    if "," in candidate and "," not in existing:
        return candidate
    if "," in existing and "," not in candidate:
        return existing
    if len(candidate) > len(existing) and not prefer_existing:
        return candidate
    return existing


def merge_role_lists(*role_lists: list[str] | None) -> list[str]:
    out: list[str] = []
    for roles in role_lists:
        for role in roles or []:
            if role and role not in out:
                out.append(role)
    return out
