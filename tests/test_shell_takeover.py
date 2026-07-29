"""Unit tests for shell-takeover pattern detection (no live Zefix)."""

from datetime import date

from app.checks.shell_takeover import detect_shell_takeover_pattern


def _pub(d: str, keys: list[str], message: str) -> dict:
    return {
        "sogcDate": d,
        "mutationTypes": [{"key": k} for k in keys],
        "message": message,
    }


def test_shell_takeover_high_confidence():
    # Old company (2012) + organ change 3 months ago + seat move 2 months ago + full officer swap
    sogc = [
        _pub(
            "2012-03-15",
            ["status.neu"],
            "Neueintragung. Eingetragene Personen: Altmann, Peter, von Zürich, in Zürich, Geschäftsführer.",
        ),
        _pub(
            "2018-06-01",
            ["adresse"],
            "Adressänderung. Eingetragene Personen: Altmann, Peter, von Zürich, in Zürich, Geschäftsführer.",
        ),
        _pub(
            "2026-04-20",
            ["aenderungorgane"],
            "Ausgeschiedene Personen: Altmann, Peter, von Zürich. "
            "Eingetragene Personen neu oder mutierend: Neuermann, Luca, von Bern, in Bern, Geschäftsführer mit Einzelunterschrift.",
        ),
        _pub(
            "2026-05-15",
            ["sitzverlegung"],
            "Sitzverlegung nach Dübendorf. "
            "Eingetragene Personen: Neuermann, Luca, von Bern, in Bern, Geschäftsführer.",
        ),
    ]
    result = detect_shell_takeover_pattern(
        sogc,
        reference=date(2026, 7, 23),
        min_age_years=5.0,
        lookback_months=12,
    )
    assert result["pattern_detected"] is True
    assert result["confidence"] == "high"
    assert result["age_years"] >= 5
    assert result["recent_organ_change"] is True
    assert result["recent_structural_change"] is True


def test_young_company_no_shell_pattern():
    sogc = [
        _pub(
            "2025-11-01",
            ["status.neu"],
            "Neueintragung. Eingetragene Personen: Test, Anna, von Zürich, Geschäftsführerin.",
        ),
        _pub(
            "2026-02-01",
            ["aenderungorgane"],
            "Eingetragene Personen: Test, Anna, von Zürich, Geschäftsführerin; Neu, Ben, von Bern, Gesellschafter.",
        ),
    ]
    result = detect_shell_takeover_pattern(sogc, reference=date(2026, 7, 23))
    assert result["pattern_detected"] is False
    assert result["confidence"] is None
