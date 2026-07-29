"""Analyze Zefix SHAB (SOGC) publications for legitimacy signals."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

MUTATION_LABELS_DE: dict[str, str] = {
    "status": "Statusänderung",
    "status.neu": "Neueintragung",
    "status.geloescht": "Löschung",
    "aenderungorgane": "Organänderung",
    "kapitalaenderung": "Kapitaländerung",
    "kapitalaenderung.erhoehung": "Kapitalerhöhung",
    "kapitalaenderung.erniedrigung": "Kapitalherabsetzung",
    "fusion": "Fusion",
    "vermoegenstransfer": "Vermögenstransfer",
    "firmenname": "Firmennamensänderung",
    "adresse": "Adressänderung",
    "sitzverlegung": "Sitzverlegung",
    "zweck": "Zweckänderung",
    "statuten": "Statutenänderung",
    "rechtsform": "Rechtsformänderung",
    "umwandlung": "Umwandlung",
    "liquidation": "Liquidation",
}

_HIGH_RISK_KEY_PARTS = (
    "adresse",
    "sitz",
    "firmenname",
    "namensaenderung",
    "fusion",
    "uebernahme",
    "übernahme",
    "vermoegenstransfer",
    "vermögenstransfer",
    "umwandlung",
    "spaltung",
    "liquidation",
    "loeschung",
    "löschung",
    "geloescht",
)

_MEDIUM_RISK_KEY_PARTS = (
    "aenderungorgane",
    "organe",
    "kapital",
    "zweck",
    "statuten",
    "rechtsform",
)

_INFO_KEY_PARTS = ("status.neu",)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _strip_ft_tags(message: str) -> str:
    text = re.sub(r"<[^>]+>", "", message or "")
    return text.replace("&amp;", "&").replace("  ", " ").strip()


def _label_for_key(key: str) -> str:
    if key in MUTATION_LABELS_DE:
        return MUTATION_LABELS_DE[key]
    for part, label in MUTATION_LABELS_DE.items():
        if key.startswith(part):
            return label
    return key.replace(".", " ").replace("_", " ").title()


def _collect_type_keys(pub: dict) -> list[str]:
    return [
        t.get("key", "")
        for t in (pub.get("mutationTypes") or [])
        if isinstance(t, dict) and t.get("key")
    ]


def _severity_for_keys(keys: list[str]) -> str:
    lowered = [k.lower() for k in keys]
    if any(k == "status.neu" for k in lowered) and len(lowered) <= 2:
        if all(k in ("status", "status.neu") for k in lowered):
            return "info"
    for key in lowered:
        if any(part in key for part in _HIGH_RISK_KEY_PARTS):
            return "high"
    for key in lowered:
        if any(part in key for part in _MEDIUM_RISK_KEY_PARTS):
            return "medium"
    return "none"


def _message_hints_high_risk(message: str) -> bool:
    text = _strip_ft_tags(message).lower()
    hints = (
        "sitzverlegung",
        "firmenname",
        "namensänderung",
        "namensaenderung",
        "fusion",
        "übernahme",
        "uebernahme",
        "vermögenstransfer",
        "vermoegenstransfer",
    )
    return any(h in text for h in hints)


def _sorted_publications(sogc_pub: list[dict]) -> list[dict]:
    pubs = [p for p in sogc_pub if isinstance(p, dict)]
    return sorted(
        pubs,
        key=lambda p: _parse_date(p.get("sogcDate")) or date.min,
        reverse=True,
    )


def _is_young_company(sorted_pubs: list[dict], reference: date) -> bool:
    if not sorted_pubs:
        return True
    oldest = _parse_date(sorted_pubs[-1].get("sogcDate"))
    if oldest and (reference - oldest).days < 730:
        return True
    if len(sorted_pubs) <= 2:
        return True
    return all(
        _severity_for_keys(_collect_type_keys(p)) in ("info", "none")
        for p in sorted_pubs
    )


def analyze_mutations(
    sogc_pub: list[dict] | None,
    *,
    old_names: list | None = None,
    has_taken_over: Any = None,
    was_taken_over_by: Any = None,
    reference: date | None = None,
) -> dict[str, Any]:
    """Return mutation analysis with score_adjustment (negative) and UI fields."""
    reference = reference or date.today()
    pubs = _sorted_publications(sogc_pub or [])
    young = _is_young_company(pubs, reference)

    warnings: list[str] = []
    breakdown: list[dict] = []
    adjustment = 0
    applied: set[str] = set()

    def deduct(points: int, label: str, flag: str | None = None) -> None:
        nonlocal adjustment
        if points <= 0:
            return
        adjustment -= points
        breakdown.append({"label": label, "points": -points, "max_points": points})
        if flag:
            warnings.append(flag)

    latest_date = _parse_date(pubs[0].get("sogcDate")) if pubs else None
    days_since = (reference - latest_date).days if latest_date else None

    recent_90 = [
        p for p in pubs
        if (d := _parse_date(p.get("sogcDate"))) and (reference - d).days <= 90
    ]
    recent_180 = [
        p for p in pubs
        if (d := _parse_date(p.get("sogcDate"))) and (reference - d).days <= 180
    ]

    if has_taken_over or was_taken_over_by:
        deduct(
            2,
            "Übernahme-/Fusionshinweis im Register",
            "Firma hat Übernahme- oder Fusionsvorgang im Handelsregister",
        )

    if old_names:
        recent_name_change = any(
            _severity_for_keys(_collect_type_keys(p)) == "high"
            and any("firmenname" in k or "name" in k for k in _collect_type_keys(p))
            for p in recent_180
        )
        if recent_name_change or (young and len(old_names) > 0):
            deduct(
                2,
                "Firmennamensänderung in den letzten 180 Tagen",
                "Kürzliche Namensänderung — Identität prüfen",
            )

    for pub in recent_180:
        keys = _collect_type_keys(pub)
        pub_date = _parse_date(pub.get("sogcDate"))
        if not pub_date:
            continue
        days_ago = (reference - pub_date).days
        severity = _severity_for_keys(keys)
        message_high = _message_hints_high_risk(pub.get("message", ""))
        key_sig = f"{pub_date}:{','.join(keys)}"

        if severity == "info":
            continue

        if severity == "high" or message_high:
            if key_sig in applied:
                continue
            applied.add(key_sig)
            if young and days_ago <= 180:
                deduct(
                    3 if days_ago <= 90 else 2,
                    f"Adress-/Namens-/Strukturänderung ({days_ago} Tage)",
                    "Kürzliche Adress- oder Namensänderung bei junger Firma",
                )
            elif days_ago <= 365 and any(
                part in k for k in keys for part in ("fusion", "vermoegen", "uebernahme", "übernahme")
            ):
                deduct(
                    2,
                    f"Fusion/Vermögenstransfer ({days_ago} Tage)",
                    "Eigentümer- oder Strukturwechsel im Handelsregister",
                )
        elif severity == "medium" and young and days_ago <= 90:
            if key_sig in applied:
                continue
            applied.add(key_sig)
            deduct(
                1,
                f"Organ-/Kapitaländerung ({days_ago} Tage)",
                None,
            )

    mutation_count_90 = len([
        p for p in recent_90
        if _severity_for_keys(_collect_type_keys(p)) not in ("info", "none")
    ])
    if young and mutation_count_90 >= 2 and "multi_90" not in applied:
        applied.add("multi_90")
        deduct(
            1,
            f"{mutation_count_90} relevante SHAB-Meldungen in 90 Tagen",
            "Ungewöhnlich viele Registeränderungen kurz hintereinander",
        )

    adjustment = max(adjustment, -4)

    recent_publications = []
    for pub in pubs[:5]:
        keys = _collect_type_keys(pub)
        recent_publications.append({
            "date": pub.get("sogcDate"),
            "types": keys,
            "types_de": [_label_for_key(k) for k in keys],
            "message_short": _strip_ft_tags(pub.get("message", ""))[:200],
            "severity": _severity_for_keys(keys),
        })

    is_new_only = bool(pubs) and all(
        _severity_for_keys(_collect_type_keys(p)) == "info" for p in pubs
    )
    if is_new_only and latest_date:
        warnings.append(
            f"Neueintragung vom {latest_date.isoformat()} — junges Unternehmen"
        )

    if not pubs:
        analysis_line = "Keine SHAB-Publikationen verfügbar"
    elif latest_date and days_since is not None:
        latest_types = ", ".join(_label_for_key(k) for k in _collect_type_keys(pubs[0]))
        analysis_line = f"Letzte SHAB-Meldung: {latest_date.isoformat()} ({latest_types or 'ohne Typ'})"
    else:
        analysis_line = f"{len(pubs)} SHAB-Meldungen im Register"

    return {
        "publication_count": len(pubs),
        "latest_mutation_date": latest_date.isoformat() if latest_date else None,
        "days_since_last_mutation": days_since,
        "recent_publications": recent_publications,
        "warnings": warnings,
        "warning_flags": warnings,
        "score_adjustment": adjustment,
        "score_breakdown": breakdown,
        "is_new_registration_only": is_new_only,
        "is_young_company": young,
        "mutation_analysis": analysis_line,
    }
