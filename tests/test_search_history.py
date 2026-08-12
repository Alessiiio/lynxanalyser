"""Unit tests for shared company search history helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from app.hr_network.search_history import _company_key, _iso_utc


def test_company_key_prefers_uid():
    assert _company_key("ACME AG", "CHE-123.456.789") == "uid:CHE-123.456.789"
    assert _company_key("ACME AG", "  che-123.456.789  ") == "uid:CHE-123.456.789"


def test_company_key_falls_back_to_name():
    assert _company_key("  ACME AG  ", None) == "name:acme ag"
    assert _company_key("", "") == ""
    assert _company_key(None, None) == ""


def test_iso_utc_appends_z_for_naive_and_aware():
    assert _iso_utc(None) is None
    assert _iso_utc(datetime(2026, 8, 6, 12, 30, 0)) == "2026-08-06T12:30:00Z"
    assert (
        _iso_utc(datetime(2026, 8, 6, 12, 30, 0, tzinfo=timezone.utc))
        == "2026-08-06T12:30:00Z"
    )
