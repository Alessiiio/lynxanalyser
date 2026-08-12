"""Unit tests for SHAB mutation severity + warning pills."""

from __future__ import annotations

from datetime import date

from app.checks.zefix_mutations import (
    _label_for_key,
    _severity_for_keys,
    analyze_mutations,
)


def _pub(sogc_date: str, *keys: str) -> dict:
    return {
        "sogcDate": sogc_date,
        "mutationTypes": [{"key": k} for k in keys],
        "message": "",
    }


def test_adressaenderung_is_high_risk():
    """Zefix uses adressaenderung — must not fall through as none/medium-only."""
    assert _severity_for_keys(["adressaenderung"]) == "high"
    assert _severity_for_keys(["adresse"]) == "high"
    assert _severity_for_keys(["adressaenderung", "aenderungorgane"]) == "high"
    assert _label_for_key("adressaenderung") == "Adressänderung"


def test_established_firm_recent_address_warns(ref=date(2026, 8, 12)):
    """Non-young firm with address change ≤90d gets a warning pill."""
    pubs = [
        _pub("2026-07-14", "adressaenderung", "aenderungorgane"),
        _pub("2024-06-05", "aenderungorgane"),
        _pub("2021-05-12", "kapitalaenderung"),
    ]
    result = analyze_mutations(pubs, reference=ref)
    assert result["is_young_company"] is False
    assert "Kürzliche Adress- oder Namensänderung" in result["warnings"]
    assert result["score_adjustment"] < 0


def test_established_firm_recent_organ_warns(ref=date(2026, 8, 12)):
    """Organänderung ≤90d warns for any firm (not only young / score-only)."""
    pubs = [
        _pub("2026-05-27", "aenderungorgane"),
        _pub("2024-06-05", "aenderungorgane"),
        _pub("2023-01-10", "kapitalaenderung"),
        _pub("2021-05-12", "kapitalaenderung"),
    ]
    result = analyze_mutations(pubs, reference=ref)
    assert result["is_young_company"] is False
    assert "Kürzliche Organänderung im Handelsregister" in result["warnings"]


def test_old_mutations_do_not_warn(ref=date(2026, 8, 12)):
    """Keep noise low: address/organ older than windows stay silent."""
    pubs = [
        _pub("2024-02-29", "adressaenderung", "aenderungorgane"),
        _pub("2021-05-12", "kapitalaenderung"),
    ]
    result = analyze_mutations(pubs, reference=ref)
    assert result["warnings"] == []
    assert result["score_adjustment"] == 0


def test_young_firm_address_keeps_stronger_flag(ref=date(2026, 8, 12)):
    pubs = [
        _pub("2026-07-01", "adressaenderung"),
        _pub("2026-01-15", "status", "status.neu"),
    ]
    result = analyze_mutations(pubs, reference=ref)
    assert result["is_young_company"] is True
    assert "Kürzliche Adress- oder Namensänderung bei junger Firma" in result["warnings"]
