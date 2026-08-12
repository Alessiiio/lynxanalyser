"""Parse persons and roles from Zefix SHAB (SOGC) publication messages."""

from __future__ import annotations

import html
import re
from typing import Any

from app.checks.zefix_mutations import _strip_ft_tags
from app.hr_network.person_names import (
    merge_role_lists,
    names_same_person,
    prefer_display_name,
)

# (keyword lowercase, UI label DE) — longer stems first when matching
_ROLE_ENTRIES: tuple[tuple[str, str], ...] = (
    # German
    ("geschäftsführerin", "Geschäftsführerin"),
    ("geschäftsführer", "Geschäftsführer"),
    ("gesellschafterin", "Gesellschafterin"),
    ("gesellschafter", "Gesellschafter"),
    ("verwaltungsrätin", "Verwaltungsrätin"),
    ("verwaltungsrat", "Verwaltungsrat"),
    ("präsidentin", "Präsidentin"),
    ("präsident", "Präsident"),
    ("vorsitzender", "Vorsitzender"),
    ("vorsitzende", "Vorsitzende"),
    ("mitglied", "Mitglied"),
    ("inhaberin", "Inhaberin"),
    ("inhaber", "Inhaber"),
    ("zeichnungsberechtigt", "Zeichnungsberechtigt"),
    ("prokuristin", "Prokuristin"),
    ("prokurist", "Prokurist"),
    ("liquidatorin", "Liquidatorin"),
    ("liquidator", "Liquidator"),
    # Italian (TI / Italian SHAB)
    ("amministratrice", "Verwaltungsrätin"),
    ("amministratore", "Verwaltungsrat"),
    ("presidentessa", "Präsidentin"),
    ("presidente", "Präsident"),
    ("liquidatrice", "Liquidatorin"),
    ("liquidatore", "Liquidator"),
    ("direttrice", "Geschäftsführerin"),
    ("direttore", "Geschäftsführer"),
    ("gerenta", "Geschäftsführerin"),
    ("gerente", "Geschäftsführer"),
    ("socia", "Gesellschafterin"),
    ("socio", "Gesellschafter"),
    # French (Romandie SHAB)
    ("administratrice", "Verwaltungsrätin"),
    ("administrateur", "Verwaltungsrat"),
    ("présidente", "Präsidentin"),
    ("président", "Präsident"),
    ("president", "Präsident"),
    ("liquidateur", "Liquidator"),
    ("directrice", "Geschäftsführerin"),
    ("directeur", "Geschäftsführer"),
    ("gérante", "Geschäftsführerin"),
    ("gérant", "Geschäftsführer"),
    ("gerante", "Geschäftsführerin"),
    ("gerant", "Geschäftsführer"),
    ("associée", "Gesellschafterin"),
    ("associé", "Gesellschafter"),
    ("associee", "Gesellschafterin"),
    ("associe", "Gesellschafter"),
    ("membre", "Mitglied"),
)

_PERSONS_HEADER = re.compile(
    r"(?:"
    r"Eingetragene Personen(?:\s+neu\s+oder\s+mutierend)?|"
    r"Persone iscritte(?:\s+nuove\s+o\s+mutanti)?|"
    r"Personnes inscrites(?:\s+nouvelles?\s+ou\s+mutantes?)?"
    r"):\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

_EXITED_PERSONS_HEADER = re.compile(
    r"(?:"
    r"Ausgeschiedene Personen(?:\s+und\s+erloschene\s+Unterschriften)?|"
    r"Persone uscite(?:\s+e\s+firme\s+estinte)?|"
    r"Personnes (?:sortantes|sorties)(?:\s+et\s+signatures?\s+(?:éteintes|eteintes)?)?"
    r"):\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

# Truncate section bodies so exit-block does not swallow following entered-block
_NEXT_SHAB_SECTION = re.compile(
    r"(?i)\b(?:"
    r"Ausgeschiedene Personen|"
    r"Eingetragene Personen|"
    r"Persone uscite|"
    r"Persone iscritte|"
    r"Personnes (?:sortantes|sorties)|"
    r"Personnes inscrites|"
    r"Publizierte Statuten|"
    r"Statutenänderung|"
    r"Zweckänderung|"
    r"Kapital(?:erhöhung|herabsetzung|änderung)|"
    r"Statuti pubblicati|"
    r"Capitale sociale|"
    r"Organo di pubblicazione|"
    r"Capital[- ]social|"
    r"Statuts publiés"
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
    # Keep insert order while skipping duplicate UI labels
    roles: list[str] = []
    seen_labels: set[str] = set()
    covered: list[tuple[int, int]] = []
    for kw, label in sorted(_ROLE_ENTRIES, key=lambda x: len(x[0]), reverse=True):
        start = 0
        while True:
            idx = lower.find(kw, start)
            if idx < 0:
                break
            end = idx + len(kw)
            # Word boundary: avoid matching «socio» inside longer tokens
            before = lower[idx - 1] if idx > 0 else " "
            after = lower[end] if end < len(lower) else " "
            if before.isalnum() or after.isalnum():
                start = idx + 1
                continue
            if any(idx < c_end and end > c_start for c_start, c_end in covered):
                start = idx + 1
                continue
            covered.append((idx, end))
            if label not in seen_labels:
                seen_labels.add(label)
                roles.append(label)
            start = end
    if not roles and re.search(
        r"mit\s+einzelunterschrift|con\s+firma\s+individuale|"
        r"avec\s+signature\s+individuelle",
        lower,
    ):
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
        # IT: «cittadino kosovaro» / FR: «ressortissant italien» / «citoyen français»
        it_fr_nat = re.search(
            r",\s*((?:cittadin[oa]|ressortissant(?:e)?|citoyen(?:ne)?)"
            r"(?:\s+(?:di|de|d'|du|des))?\s+[^,]{2,60})\s*(?=,|$)",
            segment,
            re.IGNORECASE,
        )
        if it_fr_nat:
            nationality = re.sub(r"\s+", " ", it_fr_nat.group(1)).strip(" ,.")
        else:
            # HR rarity: «staatenlos» / «staatenlose» / «apatride» / «apolide»
            stateless = re.search(
                r",\s*(staatenlose?(?:r|n)?|apatride|apolide|"
                r"ohne\s+Staatsangehörigkeit)\s*(?=,|$)",
                segment,
                re.IGNORECASE,
            )
            if stateless:
                raw = re.sub(r"\s+", " ", stateless.group(1)).strip(" ,.")
                nationality = (
                    "staatenlos"
                    if re.match(r"staatenlose?|apatride|apolide", raw, re.I)
                    else raw
                )

    # Swiss HR: «von X» / FR «originaire de X» = Heimatort; «in/à X» = Wohnort
    heimatort = None
    von_match = re.search(
        r",\s*(?:von|originaire\s+de|originari[oa]\s+di)\s+([^,]+)",
        segment,
        re.IGNORECASE,
    )
    if von_match:
        heimatort = von_match.group(1).strip()

    residence = None
    in_match = re.search(r",\s*(?:in|à|a)\s+([^,]+)", segment, re.IGNORECASE)
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


def _find_same_person_entry(
    store: dict[str, dict[str, Any]],
    *,
    pid: str | None = None,
    name: str | None = None,
) -> str | None:
    """Return store key for the same person (exact id or middle-name subset match)."""
    if pid and pid in store:
        return pid
    if name:
        for key, entry in store.items():
            if names_same_person(name, entry.get("name") or ""):
                return key
    return None


def _merge_timeline_person_fields(
    existing: dict[str, Any],
    person: dict[str, Any],
    *,
    date: str,
) -> None:
    preferred = prefer_display_name(existing.get("name"), person.get("name"))
    if preferred:
        existing["name"] = preferred
    existing["roles"] = merge_role_lists(existing.get("roles"), person.get("roles"))
    existing["last_seen"] = date or existing.get("last_seen")
    existing["source_date"] = date or existing.get("source_date")
    for key in ("residence", "nationality", "heimatort"):
        if person.get(key) and not existing.get(key):
            existing[key] = person[key]


def build_person_timeline(sogc_pub: list[dict] | None) -> list[dict[str, Any]]:
    """
    Chronological SHAB replay → current + former persons.

    Each entry: id, name, roles, residence, status (current|former),
    first_seen, last_seen, exited_date (if former).

    Middle-name variants («Michael» / «Michael Gabriel») are collapsed into one
    timeline entry so seed SHAB and later publications stay linked.
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
        for exit_person in parse_exited_person_entries(message, sogc_date=date):
            pid = exit_person.get("id")
            name = exit_person.get("name")
            match_key = _find_same_person_entry(current, pid=pid, name=name)
            if not match_key:
                continue
            left = current.pop(match_key)
            preferred = prefer_display_name(left.get("name"), name)
            if preferred:
                left["name"] = preferred
            left["status"] = "former"
            left["exited_date"] = date
            left["last_seen"] = date or left.get("last_seen")
            former[match_key] = left

        for person in parse_persons_from_message(message, sogc_date=date):
            pid = person["id"]
            name = person.get("name") or ""
            former_key = _find_same_person_entry(former, pid=pid, name=name)
            if former_key:
                # Re-entry after exit (possibly under fuller / shorter name)
                reentered = former.pop(former_key)
                _merge_timeline_person_fields(reentered, person, date=date)
                reentered["status"] = "current"
                reentered["exited_date"] = None
                current[former_key] = reentered
                continue

            match_key = _find_same_person_entry(current, pid=pid, name=name)
            if match_key:
                _merge_timeline_person_fields(current[match_key], person, date=date)
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


def compact_person_ref(person: dict[str, Any]) -> dict[str, Any]:
    """Lean person payload for timeline UI chips."""
    return {
        "name": person.get("name") or "",
        "roles": list(person.get("roles") or [])[:4],
    }


def person_changes_from_message(message: str, *, sogc_date: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Structured enter/exit lists for one SHAB publication."""
    return {
        "entered": [
            compact_person_ref(p)
            for p in parse_persons_from_message(message, sogc_date=sogc_date)
            if p.get("name")
        ],
        "exited": [
            compact_person_ref(p)
            for p in parse_exited_person_entries(message, sogc_date=sogc_date)
            if p.get("name")
        ],
    }


_SHAB_SECTION_MARKERS = re.compile(
    r"(\S)\s+(?=("
    r"Statutenänderung|"
    r"Zweckänderung|"
    r"Kapitaländerung|"
    r"Kapitalerhöhung|"
    r"Kapitalherabsetzung|"
    r"Namensänderung|"
    r"Firmenname|"
    r"Sitz\s+neu|"
    r"Zweck\s+neu|"
    r"Domizil\s+neu|"
    r"Neue\s+Adresse|"
    r"Adresse\s+neu|"
    r"Firma\s+neu|"
    r"Publizierte\s+Statuten|"
    r"Ausgeschiedene\s+Personen|"
    r"Eingetragene\s+Personen|"
    r"Persone\s+iscritte|"
    r"Persone\s+uscite|"
    r"Personnes\s+inscrites|"
    r"Personnes\s+(?:sortantes|sorties)|"
    r"Capitale\s+sociale|"
    r"Organo\s+di\s+pubblicazione"
    r")\b)",
    re.IGNORECASE,
)


def repair_mojibake(text: str) -> str:
    """Fix UTF-8 text that was mis-decoded as Latin-1 (e.g. GeschÃ¤ftsfÃ¼hrer)."""
    s = text or ""
    if not s:
        return ""
    if re.search(r"Ã.|Â.|Ä[¼¤¶]|Ã\x83", s):
        try:
            repaired = s.encode("latin-1").decode("utf-8")
            if repaired and "\ufffd" not in repaired:
                s = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return (
        s.replace("Ã¼", "ü")
        .replace("Ã¤", "ä")
        .replace("Ã¶", "ö")
        .replace("Ã©", "é")
        .replace("Ã¨", "è")
        .replace("ÃŸ", "ß")
        .replace("Ä¼", "ü")
        .replace("Ä¤", "ä")
        .replace("Ä¶", "ö")
    )


def _soft_trunc(text: str, max_len: int) -> str:
    """Truncate at a word boundary when possible (never the sole full view)."""
    if max_len <= 0 or len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    sp = cut.rfind(" ")
    if sp > max_len // 2:
        cut = cut[:sp]
    return cut.rstrip(" ,.;") + "…"


def soft_format_shab_prose(text: str) -> str:
    """Insert soft paragraph breaks before known SHAB section markers."""
    if not text:
        return ""
    out = _SHAB_SECTION_MARKERS.sub(r"\1\n\n", text)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def clean_shab_message_for_display(
    message: str,
    *,
    max_len: int | None = None,
) -> str:
    """
    Timeline expand text: prefer person sections only; drop purpose/boilerplate noise.

    Full message is returned by default (no mid-sentence hard cut). Pass max_len only
    for compact previews — truncation then prefers word boundaries.
    """
    text = _strip_ft_tags(message or "")
    text = html.unescape(text)
    text = repair_mojibake(text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    chunks: list[str] = []
    exited = _EXITED_PERSONS_HEADER.search(text)
    if exited:
        body = _section_body_after_header(exited)
        if body:
            chunks.append(f"Ausgeschieden: {body}")
    entered = _PERSONS_HEADER.search(text)
    if entered:
        body = _section_body_after_header(entered)
        if body:
            chunks.append(f"Eingetragen: {body}")

    if chunks:
        out = " · ".join(chunks)
    else:
        # Keep full cleaned SHAB prose (Zweck neu / Statuten etc. are the content)
        out = re.sub(r"\s+", " ", text).strip(" .;")
        out = soft_format_shab_prose(out)

    if max_len is not None and len(out) > max_len:
        return _soft_trunc(out, max_len)
    return out


def enrich_publication_for_timeline(pub: dict[str, Any]) -> dict[str, Any]:
    """Add persons_in / persons_out + full cleaned SHAB text for UI timeline."""
    raw = pub.get("message") or ""
    date = pub.get("sogcDate") or pub.get("shabDate")
    changes = person_changes_from_message(raw, sogc_date=date)
    cleaned = clean_shab_message_for_display(raw)
    # Full stripped message as fallback when clean dropped everything useful
    fallback = soft_format_shab_prose(
        re.sub(
            r"\s+",
            " ",
            repair_mojibake(html.unescape(_strip_ft_tags(raw or ""))),
        ).strip()
    )
    full = repair_mojibake(cleaned or fallback)
    return {
        "entered": changes["entered"],
        "exited": changes["exited"],
        "message_clean": full,
        "message_preview": _soft_trunc(full, 220) if full else "",
    }


def detect_shab_warnings(sogc_pub: list[dict] | None) -> list[str]:
    """Heuristic warnings from full SHAB text corpus."""
    warnings: list[str] = []
    pubs = [p for p in (sogc_pub or []) if isinstance(p, dict)]
    if not pubs:
        warnings.append(
            "Zefix: keine SHAB-/SOGC-Publikationen (auch zentral «No entry») — "
            "Organpersonen nicht auslesbar, kantonalen Auszug prüfen"
        )
        return warnings
    corpus = " ".join(
        _strip_ft_tags(p.get("message", ""))
        for p in pubs
    ).lower()

    if re.search(r"verzicht\w*\s+.{0,40}revision|rinuncia\b.{0,40}revisione|renonce\b.{0,40}r[eé]vision", corpus):
        warnings.append("Revisionsverzicht im SHAB erwähnt")
    if not re.search(
        r"eingetragene\s+personen|persone\s+iscritte|personnes\s+inscrites",
        corpus,
    ):
        warnings.append("Keine eingetragenen Personen im verfügbaren SHAB-Text gefunden")
    return warnings
