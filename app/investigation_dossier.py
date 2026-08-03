"""Investigation dossier PDF for a watched person (public registry snapshot + analyst case notes)."""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from app.date_format import format_datetime_display, now_display


def _draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, *, max_chars: int = 100) -> float:
    line = (text or "").replace("\n", " ")
    while line:
        chunk = line[:max_chars]
        c.drawString(x, y, chunk)
        y -= 0.4 * cm
        line = line[max_chars:]
        if y < 2.5 * cm:
            c.showPage()
            y = A4[1] - 2 * cm
            c.setFont("Helvetica", 9)
    return y


def build_investigation_dossier_pdf(
    person: dict[str, Any],
    *,
    case_note: str = "",
    prepared_by: str = "",
) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    height = A4[1]
    y = height - 2 * cm
    generated = now_display(with_tz=True)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, "Ermittlungsdossier — Personenakte")
    y -= 0.65 * cm
    c.setFont("Helvetica", 10)
    y = _draw_wrapped(
        c,
        f"Erstellt: {generated} · Von: {prepared_by or '—'} · "
        f"Nur öffentliche Registerdaten (Zefix/SHAB) + Analystennotiz",
        2 * cm,
        y,
    )
    y -= 0.25 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Person")
    y -= 0.5 * cm
    c.setFont("Helvetica", 10)
    y = _draw_wrapped(
        c,
        f"{person.get('display_name') or '—'} · Wohnort: {person.get('residence') or '—'} · "
        f"Status: {person.get('status') or '—'}",
        2 * cm,
        y,
    )
    y = _draw_wrapped(
        c,
        f"Watchlist-Grund: {person.get('source_reason') or '—'} · "
        f"Ursprungsfirma: {person.get('source_company_name') or '—'}",
        2 * cm,
        y,
    )
    y -= 0.2 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Firmenverbindungen")
    y -= 0.5 * cm
    c.setFont("Helvetica", 9)
    companies = person.get("companies") or []
    if not companies:
        y = _draw_wrapped(c, "Keine Verbindungen gespeichert.", 2 * cm, y)
    else:
        for link in companies:
            rel = link.get("relation_type") or "—"
            marker = "SEED" if link.get("is_seed_company") or rel == "seed" else (
                "NEU" if rel == "newly_found" else rel.upper()
            )
            line = (
                f"[{marker}] {link.get('name') or '—'} · {link.get('role') or '—'} · "
                f"{link.get('uid') or '—'} · {link.get('first_detected_at') or '—'}"
            )
            y = _draw_wrapped(c, line, 2 * cm, y, max_chars=105)
            if y < 3 * cm:
                c.showPage()
                y = height - 2 * cm
                c.setFont("Helvetica", 9)
    y -= 0.2 * cm

    alerts = person.get("alerts") or []
    if y < 5 * cm:
        c.showPage()
        y = height - 2 * cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Fund-Historie")
    y -= 0.5 * cm
    c.setFont("Helvetica", 9)
    if not alerts:
        y = _draw_wrapped(c, "Keine Funde.", 2 * cm, y)
    else:
        for a in alerts:
            line = (
                f"{format_datetime_display(a.get('created_at'))} · {a.get('severity') or '—'} · "
                f"{a.get('alert_type') or '—'} · {a.get('message') or '—'}"
            )
            y = _draw_wrapped(c, line, 2 * cm, y, max_chars=105)
            if y < 3 * cm:
                c.showPage()
                y = height - 2 * cm
                c.setFont("Helvetica", 9)
    y -= 0.25 * cm

    if y < 5 * cm:
        c.showPage()
        y = height - 2 * cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Fallnotiz / Ermittlungsinhalt")
    y -= 0.5 * cm
    c.setFont("Helvetica", 10)
    y = _draw_wrapped(c, case_note or person.get("case_notes") or "—", 2 * cm, y, max_chars=95)

    c.save()
    return buffer.getvalue()
