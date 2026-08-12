"""Unit tests for company tags (In Abklärung)."""

from __future__ import annotations

from app.hr_network.company_tags import TAG_LABELS_DE, TAG_UNDER_INVESTIGATION, _uid_digits


def test_uid_digits_strips_format():
    assert _uid_digits("CHE-123.456.789") == "123456789"
    assert _uid_digits("  che-123.456.789  ") == "123456789"
    assert _uid_digits(None) == ""


def test_tag_label_de():
    assert TAG_UNDER_INVESTIGATION == "under_investigation"
    assert TAG_LABELS_DE[TAG_UNDER_INVESTIGATION] == "In Abklärung"
