"""PDF name list for core-banking screening (Profiler export)."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from app.date_format import now_display


def build_profiler_screening_pdf(
    *,
    seed_name: str = "",
    seed_uid: str = "",
    companies: list[str] | None = None,
    persons: list[str] | None = None,
    prepared_by: str = "",
) -> bytes:
    companies = [c.strip() for c in (companies or []) if c and str(c).strip()]
    persons = [p.strip() for p in (persons or []) if p and str(p).strip()]
    # de-dupe preserve order
    companies = list(dict.fromkeys(companies))
    persons = list(dict.fromkeys(persons))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = height - 2 * cm
    generated = now_display(with_tz=True)

    def ensure_space(need: float = 1.2 * cm) -> None:
        nonlocal y
        if y < 2.5 * cm + need:
            c.showPage()
            y = height - 2 * cm
            c.setFont("Helvetica", 10)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, "Profiler — Namensliste Screening")
    y -= 0.55 * cm
    c.setFont("Helvetica", 9)
    c.drawString(
        2 * cm,
        y,
        f"Erstellt: {generated} · Von: {prepared_by or '—'} · Nur für internen Abgleich (Kernbank)",
    )
    y -= 0.45 * cm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, f"Fallfokus: {seed_name or '—'}")
    y -= 0.4 * cm
    c.setFont("Helvetica", 10)
    if seed_uid:
        c.drawString(2 * cm, y, f"UID: {seed_uid}")
        y -= 0.45 * cm
    y -= 0.2 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, f"Firmen / Vereine ({len(companies)})")
    y -= 0.5 * cm
    c.setFont("Helvetica", 10)
    if not companies:
        c.drawString(2 * cm, y, "—")
        y -= 0.4 * cm
    else:
        for name in companies:
            ensure_space()
            c.drawString(2 * cm, y, name[:110])
            y -= 0.38 * cm

    y -= 0.35 * cm
    ensure_space(1.5 * cm)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, f"Personen ({len(persons)})")
    y -= 0.5 * cm
    c.setFont("Helvetica", 10)
    if not persons:
        c.drawString(2 * cm, y, "—")
        y -= 0.4 * cm
    else:
        for name in persons:
            ensure_space()
            c.drawString(2 * cm, y, name[:110])
            y -= 0.38 * cm

    y -= 0.5 * cm
    ensure_space(1.2 * cm)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        2 * cm,
        y,
        "Hinweis: Öffentliche Registerdaten / Analystenkenntnis — kein Kundenbeziehungsnachweis.",
    )

    c.save()
    return buf.getvalue()
