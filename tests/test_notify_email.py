"""Watchlist e-mail notification helpers (no live SMTP)."""

from __future__ import annotations

from app.notify_email import (
    build_watchlist_hits_email,
    notify_watchlist_new_hits,
    send_email,
    smtp_configured,
)


def test_smtp_configured_false_by_default():
    assert smtp_configured() is False


def test_send_email_skips_without_smtp(monkeypatch):
    import config

    monkeypatch.setattr(config, "SMTP_HOST", "")
    monkeypatch.setattr(config, "SMTP_FROM", "")
    monkeypatch.setattr(config, "WATCHLIST_NOTIFY_EMAILS", ["a@example.com"])
    out = send_email(subject="t", body="b")
    assert out["sent"] is False
    assert out["reason"] == "smtp_unset"


def test_notify_skips_without_recipients(monkeypatch):
    import config

    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_FROM", "lynx@example.com")
    monkeypatch.setattr(config, "WATCHLIST_NOTIFY_EMAILS", [])
    out = notify_watchlist_new_hits(
        [{"person_name": "Muster", "company_name": "X AG", "alert_type": "new_role"}],
        source="test",
    )
    assert out["sent"] is False
    assert out["reason"] == "recipients_unset"
    assert out["alert_count"] == 1


def test_build_email_body_includes_inbox_hint():
    subject, body = build_watchlist_hits_email(
        alerts=[
            {
                "person_name": "Meier, Anna",
                "company_name": "Shell GmbH",
                "alert_type": "new_company_founded",
                "severity": "high",
                "message": "test msg",
            }
        ],
        source="manual_person_scan",
    )
    assert "1 neuer Fund" in subject
    assert "Meier, Anna" in body
    assert "Shell GmbH" in body
    assert "manual_person_scan" in body
    assert "tab=inbox" in body
