"""Detect shell-company takeover / money-mule preparation patterns in SHAB history."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.checks.zefix_mutations import (
    _collect_type_keys,
    _parse_date,
    _sorted_publications,
    _strip_ft_tags,
)
from app.hr_network.shab_parser import build_person_timeline, parse_exited_persons_from_message


_ORGAN_KEY_PARTS = ("aenderungorgane", "organe")
_STRUCTURAL_KEY_PARTS = ("sitz", "adresse", "zweck", "firmenname", "name")


def _keys_match(keys: list[str], parts: tuple[str, ...]) -> bool:
    lowered = [k.lower() for k in keys]
    return any(any(part in k for part in parts) for k in lowered)


def _message_hints_organ(message: str) -> bool:
    text = _strip_ft_tags(message).lower()
    return any(
        h in text
        for h in (
            "eingetragene personen",
            "ausgeschiedene personen",
            "geschäftsführer",
            "verwaltungsrat",
            "gesellschafter",
        )
    )


def _message_hints_structural(message: str) -> bool:
    text = _strip_ft_tags(message).lower()
    return any(
        h in text
        for h in ("sitzverlegung", "zweck", "domizil", "firmenname", "namensänderung", "namensaenderung")
    )


def detect_shell_takeover_pattern(
    sogc_pub: list[dict] | None,
    *,
    reference: date | None = None,
    min_age_years: float = 5.0,
    lookback_months: int = 12,
) -> dict[str, Any]:
    """
    Erkennt: altes Unternehmen + kürzlicher Organwechsel (+ optional Sitz-/Zweckänderung).

    Gegenteil von «junge Firma tarnt sich alt» — hier: etablierte Firma mit plötzlichem Wechsel.
    """
    reference = reference or date.today()
    pubs = _sorted_publications(sogc_pub or [])
    empty = {
        "pattern_detected": False,
        "confidence": None,
        "age_years": None,
        "recent_events": [],
        "recent_organ_change": False,
        "recent_structural_change": False,
        "all_officers_replaced": False,
        "new_officer_slugs": [],
        "reason": "Keine SHAB-Publikationen",
    }
    if not pubs:
        return empty

    oldest = _parse_date(pubs[-1].get("sogcDate"))
    if not oldest:
        return {**empty, "reason": "Kein gültiges ältestes sogcDate"}

    age_years = round((reference - oldest).days / 365.25, 2)
    lookback_start = reference - timedelta(days=int(lookback_months * 30.44))
    recent = [
        p for p in pubs
        if (d := _parse_date(p.get("sogcDate"))) and lookback_start <= d <= reference
    ]

    recent_events: list[dict[str, Any]] = []
    recent_organ = False
    recent_structural = False

    for pub in recent:
        keys = _collect_type_keys(pub)
        msg = pub.get("message") or ""
        organ = _keys_match(keys, _ORGAN_KEY_PARTS) or _message_hints_organ(msg)
        structural = _keys_match(keys, _STRUCTURAL_KEY_PARTS) or _message_hints_structural(msg)
        if organ:
            recent_organ = True
        if structural:
            recent_structural = True
        if organ or structural:
            recent_events.append({
                "date": pub.get("sogcDate"),
                "types": keys,
                "organ": organ,
                "structural": structural,
            })

    # Officer replacement: compare timeline status using exited vs new within window.
    timeline = build_person_timeline(sogc_pub)
    current = [p for p in timeline if p.get("status") == "current"]
    former = [p for p in timeline if p.get("status") == "former"]

    exited_in_window = [
        p for p in former
        if (d := _parse_date(p.get("exited_date"))) and lookback_start <= d <= reference
    ]
    entered_in_window = [
        p for p in current
        if (d := _parse_date(p.get("first_seen"))) and lookback_start <= d <= reference
    ]

    # Strong signal: previous officers exited in window AND current officers only appeared then.
    all_officers_replaced = False
    if exited_in_window and entered_in_window:
        # If every current officer first appeared in the window, and at least one left — treat as replacement.
        if current and all(
            (d := _parse_date(p.get("first_seen"))) and lookback_start <= d <= reference
            for p in current
        ):
            all_officers_replaced = True
        # Or: exited count covers former current set size heuristic
        elif len(exited_in_window) >= max(1, len(entered_in_window)):
            # Also check messages for Ausgeschiedene in window
            exited_msgs = 0
            for pub in recent:
                if parse_exited_persons_from_message(pub.get("message") or ""):
                    exited_msgs += 1
            if exited_msgs > 0:
                all_officers_replaced = True

    new_officer_slugs = [p.get("id") for p in entered_in_window if p.get("id")]

    age_ok = age_years >= min_age_years
    conditions = sum([
        bool(age_ok),
        bool(recent_organ or all_officers_replaced),
        bool(recent_structural or all_officers_replaced),
    ])

    pattern_detected = age_ok and (recent_organ or all_officers_replaced)
    confidence = None
    reason = "Kein Übernahme-Muster"

    if pattern_detected:
        if age_ok and (recent_organ or all_officers_replaced) and (
            recent_structural or all_officers_replaced
        ):
            confidence = "high"
            reason = (
                f"Alte Firma ({age_years} J.) mit Organwechsel"
                + (" und Sitz-/Zweck-/Namensänderung" if recent_structural else "")
                + (" — vollständiger Organersatz" if all_officers_replaced else "")
                + f" in den letzten {lookback_months} Monaten"
            )
        elif conditions >= 2:
            confidence = "medium"
            reason = (
                f"Alte Firma ({age_years} J.) mit verdächtigem Wechsel "
                f"(Organ={recent_organ}, Struktur={recent_structural}, Ersatz={all_officers_replaced})"
            )
        else:
            pattern_detected = False
            confidence = None
            reason = "Nur einzelne Indizien — unter Schwelle"

    return {
        "pattern_detected": pattern_detected,
        "confidence": confidence,
        "age_years": age_years,
        "company_first_seen": oldest.isoformat(),
        "recent_events": recent_events,
        "recent_organ_change": recent_organ,
        "recent_structural_change": recent_structural,
        "all_officers_replaced": all_officers_replaced,
        "new_officer_slugs": new_officer_slugs,
        "new_officers": [
            {"id": p.get("id"), "name": p.get("name"), "first_seen": p.get("first_seen"), "roles": p.get("roles")}
            for p in entered_in_window
        ],
        "reason": reason,
    }
