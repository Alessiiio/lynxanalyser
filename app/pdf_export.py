"""Generate a simple PDF report from a FullReport."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from app.date_format import now_display
from app.models import FullReport


def build_pdf_report(report: FullReport) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 2 * cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, "Website-Prüfbericht")
    y -= 0.8 * cm

    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, y, f"Erstellt: {now_display(with_tz=True)}")
    y -= 0.6 * cm
    c.drawString(2 * cm, y, f"URL: {report.url}")
    y -= 0.5 * cm
    c.drawString(2 * cm, y, f"Domain: {report.domain}")
    y -= 0.8 * cm

    c.setFont("Helvetica-Bold", 14)
    c.drawString(2 * cm, y, f"Score: {report.total_score}/100 — {report.verdict}")
    y -= 0.8 * cm

    if report.goldlist_match:
        c.setFont("Helvetica-Oblique", 10)
        c.drawString(2 * cm, y, "Hinweis: Domain steht auf interner Goldlist")
        y -= 0.6 * cm

    if report.critical_flags:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(2 * cm, y, "Kritische Warnhinweise:")
        y -= 0.5 * cm
        c.setFont("Helvetica", 10)
        for flag in report.critical_flags:
            c.drawString(2.3 * cm, y, f"• {flag}")
            y -= 0.45 * cm
        y -= 0.2 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Einzelchecks:")
    y -= 0.55 * cm
    c.setFont("Helvetica", 9)

    for check in report.checks:
        if y < 3 * cm:
            c.showPage()
            y = height - 2 * cm
            c.setFont("Helvetica", 9)
        line = f"{check.display_name}: {check.summary} (+{check.score}/{check.max_score})"
        if len(line) > 110:
            line = line[:107] + "..."
        c.drawString(2 * cm, y, line)
        y -= 0.42 * cm

    c.save()
    return buffer.getvalue()
