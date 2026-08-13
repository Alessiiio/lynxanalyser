"""SMTP helpers for watchlist hit notifications.

Gracefully skips when SMTP or recipients are unset — monitoring still works in-app.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import config

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    return bool(
        config.SMTP_HOST
        and config.SMTP_FROM
        and config.WATCHLIST_NOTIFY_EMAILS
    )


def _recipients() -> list[str]:
    return list(config.WATCHLIST_NOTIFY_EMAILS)


def _watchlist_base_url() -> str:
    domain = (config.DOMAIN or "").strip()
    if domain:
        scheme = "https" if config.HTTPS_ONLY else "http"
        return f"{scheme}://{domain}/watchlist"
    return "/watchlist"


def build_watchlist_hits_email(
    *,
    alerts: list[dict[str, Any]],
    source: str = "monitoring",
) -> tuple[str, str]:
    """Return (subject, plain-text body) for new watchlist findings."""
    n = len(alerts)
    subject = f"Lynx Watchlist: {n} neuer Fund" if n == 1 else f"Lynx Watchlist: {n} neue Funde"
    lines = [
        "Lynx Personen-Watchlist — neue Mandate / Firmenverknüpfungen",
        f"Auslöser: {source}",
        "",
    ]
    for i, a in enumerate(alerts, 1):
        person = a.get("person_name") or a.get("display_name") or "?"
        company = a.get("company_name") or "?"
        atype = a.get("alert_type") or "alert"
        sev = a.get("severity") or ""
        conf = a.get("confidence") or ""
        src = a.get("source") or ""
        msg = a.get("message") or ""
        lines.append(f"{i}. {person} → {company}")
        lines.append(f"   Typ: {atype}" + (f" · Severity: {sev}" if sev else ""))
        if conf or src:
            lines.append(
                "   "
                + " · ".join(
                    x
                    for x in (
                        f"Konfidenz: {conf}" if conf else "",
                        f"Quelle: {src}" if src else "",
                    )
                    if x
                )
            )
        if msg:
            lines.append(f"   {msg}")
        lines.append("")
    lines.append(f"Posteingang: {_watchlist_base_url()}?tab=inbox")
    lines.append("")
    lines.append(
        "(E-Mail nur bei neuen Funden. Ohne SMTP-Konfiguration wird nichts versendet.)"
    )
    return subject, "\n".join(lines)


def send_email(*, subject: str, body: str, to: list[str] | None = None) -> dict[str, Any]:
    """
    Send a plain-text email via SMTP.

    Returns status dict. Never raises for missing config — logs and skips.
    """
    recipients = [r for r in (to or _recipients()) if r]
    if not config.SMTP_HOST or not config.SMTP_FROM:
        logger.info("SMTP nicht konfiguriert — E-Mail übersprungen: %s", subject)
        return {"sent": False, "reason": "smtp_unset"}
    if not recipients:
        logger.info("Keine Empfänger (WATCHLIST_NOTIFY_EMAILS) — E-Mail übersprungen")
        return {"sent": False, "reason": "recipients_unset"}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        if config.SMTP_USE_TLS:
            context = ssl.create_default_context()
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                if config.SMTP_USER:
                    server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
                if config.SMTP_USER:
                    server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.send_message(msg)
    except Exception:
        logger.exception("SMTP-Versand fehlgeschlagen: %s", subject)
        return {"sent": False, "reason": "smtp_error"}

    logger.info("E-Mail gesendet an %s: %s", recipients, subject)
    return {"sent": True, "recipients": recipients}


def notify_watchlist_new_hits(
    alerts: list[dict[str, Any]],
    *,
    source: str = "monitoring",
) -> dict[str, Any]:
    """Notify configured recipients about new NetworkAlert-style findings."""
    if not alerts:
        return {"sent": False, "reason": "no_alerts"}
    subject, body = build_watchlist_hits_email(alerts=alerts, source=source)
    result = send_email(subject=subject, body=body)
    result["alert_count"] = len(alerts)
    return result
