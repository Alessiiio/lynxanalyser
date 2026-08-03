"""Display date formatting: DD-MM-YYYY everywhere in user-facing output."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DOT_DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})")
_DASH_DATE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})")


def format_date_display(value: Any, *, empty: str = "—") -> str:
    """Format any date-like value as DD-MM-YYYY for UI/PDF/Markdown."""
    if value is None or value == "":
        return empty
    if isinstance(value, datetime):
        return f"{value.day:02d}-{value.month:02d}-{value.year}"
    if isinstance(value, date):
        return f"{value.day:02d}-{value.month:02d}-{value.year}"

    s = str(value).strip()
    if not s or s in {"—", "-", "n/a", "N/A"}:
        return empty

    m = _DASH_DATE.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DOT_DATE.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _ISO_DATE.match(s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    try:
        # Handle ISO datetime
        if "T" in s or " " in s:
            raw = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            return f"{dt.day:02d}-{dt.month:02d}-{dt.year}"
    except ValueError:
        pass
    return s


def format_datetime_display(value: Any, *, empty: str = "—", with_tz: bool = False) -> str:
    """Format datetime as DD-MM-YYYY HH:MM (optional UTC suffix)."""
    if value is None or value == "":
        return empty
    if isinstance(value, datetime):
        dt = value
        base = f"{dt.day:02d}-{dt.month:02d}-{dt.year} {dt.hour:02d}:{dt.minute:02d}"
        return f"{base} UTC" if with_tz else base

    s = str(value).strip()
    if not s:
        return empty
    try:
        raw = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        base = f"{dt.day:02d}-{dt.month:02d}-{dt.year} {dt.hour:02d}:{dt.minute:02d}"
        return f"{base} UTC" if with_tz else base
    except ValueError:
        d = format_date_display(s, empty="")
        return d or empty


def now_display(*, with_tz: bool = True) -> str:
    return format_datetime_display(datetime.now(timezone.utc), with_tz=with_tz)
