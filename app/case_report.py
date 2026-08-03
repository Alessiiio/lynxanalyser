"""PDF report for a CompanyCase (investigation / compliance handoff)."""

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


def build_company_case_report(
    case: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
    prepared_by: str = "",
) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    height = A4[1]
    y = height - 2 * cm
    generated = now_display(with_tz=True)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, "Fallbericht — Company Case")
    y -= 0.65 * cm
    c.setFont("Helvetica", 10)
    y = _draw_wrapped(
        c,
        f"Case #{case.get('id')} · {generated} · Erstellt von: {prepared_by or '—'}",
        2 * cm,
        y,
    )
    y -= 0.2 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Firma")
    y -= 0.45 * cm
    c.setFont("Helvetica", 10)
    y = _draw_wrapped(
        c,
        f"{case.get('company_name')} · UID: {case.get('company_uid') or '—'} · "
        f"Typ: {case.get('fraud_type') or '—'} · Status: {case.get('status')}",
        2 * cm,
        y,
    )
    if case.get("company_purpose"):
        y = _draw_wrapped(c, f"Zweck: {case.get('company_purpose')}", 2 * cm, y)
    y = _draw_wrapped(
        c,
        f"Eröffnet: {case.get('opened_by')} · {case.get('opened_at')} · "
        f"Bestätigt: {case.get('confirmed_at') or '—'}",
        2 * cm,
        y,
    )
    y = _draw_wrapped(
        c,
        f"Zahlung blockiert: {case.get('payment_blocked')} · "
        f"Ref: {case.get('payment_blocked_note') or '—'}",
        2 * cm,
        y,
    )
    hit_bits = []
    if case.get("hit_amount") is not None:
        hit_bits.append(f"{case.get('hit_amount')} {case.get('hit_currency') or 'CHF'}")
    if case.get("hit_reference"):
        hit_bits.append(f"Zweck/Ref: {case.get('hit_reference')}")
    if case.get("hit_note"):
        hit_bits.append(case.get("hit_note"))
    if hit_bits:
        y = _draw_wrapped(c, "Zahlungs-Hit: " + " · ".join(str(b) for b in hit_bits), 2 * cm, y)
    y -= 0.2 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Journal")
    y -= 0.45 * cm
    c.setFont("Helvetica", 9)
    journal = case.get("journal") or []
    if not journal:
        y = _draw_wrapped(c, "Keine Einträge.", 2 * cm, y)
    else:
        for e in journal:
            y = _draw_wrapped(
                c,
                f"{format_datetime_display(e.get('created_at'))} · {e.get('author')}: {e.get('text')}",
                2 * cm,
                y,
                max_chars=105,
            )
            if y < 3 * cm:
                c.showPage()
                y = height - 2 * cm
                c.setFont("Helvetica", 9)
    y -= 0.2 * cm

    if y < 5 * cm:
        c.showPage()
        y = height - 2 * cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Bankbeziehungs-Checkliste")
    y -= 0.45 * cm
    c.setFont("Helvetica", 9)
    for item in case.get("bank_checks") or []:
        y = _draw_wrapped(
            c,
            f"[{item.get('status')}] {item.get('entity_type')} · {item.get('entity_label')} · "
            f"{item.get('checked_by') or '—'} · {item.get('note') or '—'}",
            2 * cm,
            y,
            max_chars=105,
        )
        if y < 3 * cm:
            c.showPage()
            y = height - 2 * cm
            c.setFont("Helvetica", 9)
    y -= 0.2 * cm

    if snapshot and not snapshot.get("error"):
        if y < 5 * cm:
            c.showPage()
            y = height - 2 * cm
        c.setFont("Helvetica-Bold", 12)
        c.drawString(2 * cm, y, "Netzwerk-Snapshot")
        y -= 0.45 * cm
        c.setFont("Helvetica", 9)
        stats = snapshot.get("stats") or {}
        y = _draw_wrapped(
            c,
            f"Knoten: {stats.get('node_count')} · Personen aktuell: {stats.get('current_persons')} · "
            f"ehemalig: {stats.get('former_persons')}",
            2 * cm,
            y,
        )
        for sc in (snapshot.get("seed_companies") or [])[:12]:
            y = _draw_wrapped(
                c,
                f"Seed: {sc.get('name')} · {sc.get('uid') or '—'}",
                2 * cm,
                y,
            )

    c.save()
    return buffer.getvalue()


def build_bank_lookup_sheet(case: dict[str, Any]) -> bytes:
    """
    One-page checklist PDF: company + persons to look up in core banking
    before answering «Kundenbeziehung».
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    height = A4[1]
    y = height - 2 * cm
    generated = now_display(with_tz=True)

    c.setFont("Helvetica-Bold", 15)
    c.drawString(2 * cm, y, "Abgleichsliste — Kernbanksysteme")
    y -= 0.55 * cm
    c.setFont("Helvetica", 9)
    y = _draw_wrapped(
        c,
        f"Akte #{case.get('id')} · {generated} · Nur interne Abklärung (keine Kundendaten)",
        2 * cm,
        y,
        max_chars=95,
    )
    y -= 0.25 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Firma (Seed)")
    y -= 0.45 * cm
    c.setFont("Helvetica", 10)
    y = _draw_wrapped(c, f"Name: {case.get('company_name') or '—'}", 2 * cm, y)
    y = _draw_wrapped(c, f"UID: {case.get('company_uid') or '—'}", 2 * cm, y)
    if case.get("company_ehraid"):
        y = _draw_wrapped(c, f"EHRAID: {case.get('company_ehraid')}", 2 * cm, y)
    y -= 0.35 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Zu prüfende Entitäten (Checkliste)")
    y -= 0.5 * cm
    c.setFont("Helvetica", 10)

    checks = case.get("bank_checks") or []
    if not checks:
        y = _draw_wrapped(
            c,
            "Noch keine Checkliste — nach Betrugsbestätigung werden Firma und aktuelle Organe angelegt.",
            2 * cm,
            y,
        )
    else:
        companies = [i for i in checks if i.get("entity_type") == "company"]
        persons = [i for i in checks if i.get("entity_type") == "person"]
        other = [i for i in checks if i.get("entity_type") not in ("company", "person")]

        def _section(title: str, items: list[dict[str, Any]]) -> float:
            nonlocal y
            if not items:
                return y
            c.setFont("Helvetica-Bold", 10)
            c.drawString(2 * cm, y, title)
            y -= 0.4 * cm
            c.setFont("Helvetica", 10)
            for idx, item in enumerate(items, 1):
                label = item.get("entity_label") or "—"
                ref = item.get("entity_ref") or ""
                status = item.get("status") or "pending"
                line = f"{idx}. {label}"
                if ref:
                    line += f"  ·  Ref: {ref}"
                line += f"  ·  Status: {status}"
                y = _draw_wrapped(c, line, 2.3 * cm, y, max_chars=92)
                y -= 0.08 * cm
            y -= 0.25 * cm
            return y

        y = _section("Firmen", companies)
        y = _section("Personen", persons)
        y = _section("Weitere", other)

    y -= 0.2 * cm
    c.setFont("Helvetica-Oblique", 8)
    y = _draw_wrapped(
        c,
        "Hinweis: Namen aus dem Handelsregister / der Akte — zum Abgleich in den Kernbanksystemen. "
        "Keine Kunden-PII auf diesem Blatt.",
        2 * cm,
        y,
        max_chars=100,
    )

    c.save()
    return buffer.getvalue()
