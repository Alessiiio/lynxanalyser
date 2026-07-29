"""Parse persons and roles from Zefix SHAB (SOGC) publication messages."""

from __future__ import annotations

import re
from typing import Any

from app.checks.zefix_mutations import _strip_ft_tags

_ROLE_KEYWORDS = (
    "geschäftsführer",
    "geschäftsführerin",
    "gesellschafter",
    "gesellschafterin",
    "verwaltungsrat",
    "präsident",
    "präsidentin",
    "mitglied",
    "inhaber",
    "inhaberin",
    "zeichnungsberechtigt",
    "prokurist",
    "liquidator",
    "liquidatorin",
    "vorsitzender",
    "vorsitzende",
)

_PERSONS_HEADER = re.compile(
    r"Eingetragene Personen(?:\s+neu\s+oder\s+mutierend)?:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

_EXITED_PERSONS_HEADER = re.compile(
    r"Ausgeschiedene Personen(?:\s+und\s+erloschene\s+Unterschriften)?:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

# Truncate section bodies so «Ausgeschiedene…» does not swallow following «Eingetragene…»
_NEXT_SHAB_SECTION = re.compile(
    r"(?i)\b(?:"
    r"Ausgeschiedene Personen|"
    r"Eingetragene Personen|"
    r"Publizierte Statuten|"
    r"Statutenänderung|"
    r"Zweckänderung|"
    r"Kapital(?:erhöhung|herabsetzung|änderung)"
    r")\b",
)


def _section_body_after_header(match: re.Match[str]) -> str:
    """Return SHAB section text, stopping before the next major section header."""
    body = match.group(1) or ""
    stop = _NEXT_SHAB_SECTION.search(body)
    if stop and stop.start() > 0:
        body = body[: stop.start()]
    return body.strip()


def _normalize_person_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return slug.strip("-")


def _extract_roles(segment: str) -> list[str]:
    """Extract HR roles; prefer longer matches so «Gesellschafterin» ≠ «Gesellschafter»."""
    lower = segment.lower()
    roles: list[str] = []
    covered: list[tuple[int, int]] = []
    for kw in sorted(_ROLE_KEYWORDS, key=len, reverse=True):
        start = 0
        while True:
            idx = lower.find(kw, start)
            if idx < 0:
                break
            end = idx + len(kw)
            if any(idx < c_end and end > c_start for c_start, c_end in covered):
                start = idx + 1
                continue
            covered.append((idx, end))
            roles.append(kw.capitalize())
            start = end
    if not roles and "mit einzelunterschrift" in lower:
        roles.append("Zeichnungsberechtigt")
    return roles


_FEMALE_ROLE_RE = re.compile(
    r"(?i)\b("
    r"geschäftsführerin|gesellschafterin|inhaberin|prokuristin|"
    r"präsidentin|direktorin|liquidatorin|vertreterin|"
    r"einzelunternehmerin|vorsitzende"
    r")\b"
)
_MALE_ROLE_RE = re.compile(
    r"(?i)\b("
    r"geschäftsführer|gesellschafter|inhaber|prokurist|"
    r"präsident|direktor|liquidator|vertreter|"
    r"einzelunternehmer|vorsitzender"
    r")(?!in)\b"
)


def infer_person_gender(roles: list[str] | None) -> str | None:
    """
    Infer gender from German HR titles (m/f).

    Feminine forms in SHAB are authoritative; otherwise masculine stems.
    Returns «m», «f», or None when titles are gender-neutral.
    """
    joined = " ".join(roles or [])
    if not joined.strip():
        return None
    if _FEMALE_ROLE_RE.search(joined):
        return "f"
    if _MALE_ROLE_RE.search(joined):
        return "m"
    return None


def _parse_person_segment(segment: str) -> dict[str, Any] | None:
    segment = segment.strip().rstrip(".")
    # Mutation notes: «... [bisher: syrischer Staatsangehöriger, ...]»
    segment = re.sub(r"\s*\[bisher:.*$", "", segment, flags=re.IGNORECASE).strip()
    if len(segment) < 8:
        return None

    # Typical: "Last, First Middle, italienische Staatsangehörige, in Ettingen, Role, ..."
    # or Swiss: "Last, First, von Zürich, in Basel, Geschäftsführer, ..."
    # or rare: "Last, First, staatenlos, in Zürich, ..."
    name_match = re.match(r"^([A-Za-zÀ-ÿ][^,]+,\s*[A-Za-zÀ-ÿ][^,]+)", segment)
    if not name_match:
        return None

    name = name_match.group(1).strip()
    if len(name) < 5:
        return None

    nationality = None
    nat_match = re.search(
        r",\s*([^,]{3,80}?Staatsangehörige(?:r|n)?)\s*(?=,|$)",
        segment,
        re.IGNORECASE,
    )
    if nat_match:
        nationality = re.sub(r"\s+", " ", nat_match.group(1)).strip(" ,.")
    else:
        # HR rarity: «staatenlos» / «staatenlose» instead of nationality or Heimatort
        stateless = re.search(
            r",\s*(staatenlose?(?:r|n)?|apatride|ohne\s+Staatsangehörigkeit)\s*(?=,|$)",
            segment,
            re.IGNORECASE,
        )
        if stateless:
            raw = re.sub(r"\s+", " ", stateless.group(1)).strip(" ,.")
            nationality = "staatenlos" if re.match(r"staatenlose?", raw, re.I) else raw

    # Swiss HR: «von X» = Heimatort, «in X» = Wohnort at publication time
    heimatort = None
    von_match = re.search(r",\s*von\s+([^,]+)", segment, re.IGNORECASE)
    if von_match:
        heimatort = von_match.group(1).strip()

    residence = None
    in_match = re.search(r",\s*in\s+([^,]+)", segment, re.IGNORECASE)
    if in_match:
        residence = in_match.group(1).strip()
    elif heimatort:
        # Fallback when only Heimatort is present
        residence = heimatort

    return {
        "name": name,
        "id": _normalize_person_id(name),
        "roles": _extract_roles(segment),
        "nationality": nationality,
        "residence": residence,
        "heimatort": heimatort,
        "raw_segment": segment[:240],
    }


def _split_person_segments(block: str) -> list[str]:
    """Split SHAB person block; segments may contain nested [bisher: ...] clauses."""
    segments: list[str] = []
    for part in block.split(";"):
        part = part.strip()
        if not part:
            continue
        # Drop trailing mutation note: «... [bisher: ...]»
        part = re.sub(r"\s*\[bisher:.*$", "", part, flags=re.IGNORECASE).strip()
        if part:
            segments.append(part)
    return segments


def parse_exited_persons_from_message(message: str) -> list[str]:
    """Return normalized person ids that left the company per SHAB text."""
    text = _strip_ft_tags(message)
    match = _EXITED_PERSONS_HEADER.search(text)
    if not match:
        return []
    ids: list[str] = []
    for segment in _split_person_segments(_section_body_after_header(match)):
        person = _parse_person_segment(segment)
        if person:
            ids.append(person["id"])
    return ids


def parse_persons_from_message(message: str, *, sogc_date: str | None = None) -> list[dict[str, Any]]:
    """Return person dicts from a single SHAB message (Eingetragene Personen)."""
    text = _strip_ft_tags(message)
    match = _PERSONS_HEADER.search(text)
    if not match:
        return []

    persons: list[dict[str, Any]] = []
    for segment in _split_person_segments(_section_body_after_header(match)):
        person = _parse_person_segment(segment)
        if person:
            person["source_date"] = sogc_date
            person["section"] = "current"
            persons.append(person)
    return persons


def parse_exited_person_entries(message: str, *, sogc_date: str | None = None) -> list[dict[str, Any]]:
    """Return person dicts from «Ausgeschiedene Personen» in a SHAB message."""
    text = _strip_ft_tags(message)
    match = _EXITED_PERSONS_HEADER.search(text)
    if not match:
        return []

    persons: list[dict[str, Any]] = []
    for segment in _split_person_segments(_section_body_after_header(match)):
        person = _parse_person_segment(segment)
        if person:
            person["source_date"] = sogc_date
            person["section"] = "exited"
            persons.append(person)
    return persons


def iter_named_persons_in_message(message: str, *, sogc_date: str | None = None) -> list[dict[str, Any]]:
    """All named person entries from Eingetragene + Ausgeschiedene blocks."""
    return parse_persons_from_message(message, sogc_date=sogc_date) + parse_exited_person_entries(
        message, sogc_date=sogc_date
    )


def collect_persons_from_publications(sogc_pub: list[dict] | None) -> list[dict[str, Any]]:
    """Reconstruct current persons from SHAB history (respects «Ausgeschiedene Personen»)."""
    return [p for p in build_person_timeline(sogc_pub) if p.get("status") == "current"]


def build_person_timeline(sogc_pub: list[dict] | None) -> list[dict[str, Any]]:
    """
    Chronological SHAB replay → current + former persons.

    Each entry: id, name, roles, residence, status (current|former),
    first_seen, last_seen, exited_date (if former).
    """
    current: dict[str, dict[str, Any]] = {}
    former: dict[str, dict[str, Any]] = {}

    pubs = sorted(
        [p for p in (sogc_pub or []) if isinstance(p, dict)],
        key=lambda p: p.get("sogcDate") or "",
    )
    for pub in pubs:
        date = pub.get("sogcDate") or ""
        message = pub.get("message", "")

        # Exits first, then entries — re-entry in the same publication stays current
        for pid in parse_exited_persons_from_message(message):
            if pid not in current:
                continue
            left = current.pop(pid)
            left["status"] = "former"
            left["exited_date"] = date
            left["last_seen"] = date or left.get("last_seen")
            former[pid] = left

        for person in parse_persons_from_message(message, sogc_date=date):
            pid = person["id"]
            if pid in former:
                # Re-entry after exit
                former.pop(pid, None)
            existing = current.get(pid)
            if existing:
                for role in person.get("roles") or []:
                    if role not in existing.setdefault("roles", []):
                        existing["roles"].append(role)
                existing["last_seen"] = date or existing.get("last_seen")
                existing["source_date"] = date or existing.get("source_date")
                if person.get("residence"):
                    existing["residence"] = person["residence"]
                if person.get("nationality"):
                    existing["nationality"] = person["nationality"]
                if person.get("heimatort"):
                    existing["heimatort"] = person["heimatort"]
            else:
                current[pid] = {
                    **person,
                    "status": "current",
                    "first_seen": date,
                    "last_seen": date,
                    "exited_date": None,
                }

    result = list(current.values()) + list(former.values())
    result.sort(key=lambda p: (0 if p.get("status") == "current" else 1, p.get("name") or ""))
    return result


def detect_shab_warnings(sogc_pub: list[dict] | None) -> list[str]:
    """Heuristic warnings from full SHAB text corpus."""
    warnings: list[str] = []
    corpus = " ".join(
        _strip_ft_tags(p.get("message", ""))
        for p in (sogc_pub or [])
        if isinstance(p, dict)
    ).lower()

    if "verzicht" in corpus and "revision" in corpus:
        warnings.append("Revisionsverzicht im SHAB erwähnt")
    if "eingetragene personen" not in corpus and sogc_pub:
        warnings.append("Keine eingetragenen Personen im verfügbaren SHAB-Text gefunden")
    return warnings
