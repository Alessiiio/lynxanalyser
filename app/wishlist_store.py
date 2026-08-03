"""Persist product wishlist / feedback items (JSON + mirrored WISHLIST.md)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.date_format import format_date_display

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
WISHLIST_JSON = _ROOT / "data" / "wishlist.json"
WISHLIST_MD = _ROOT / "WISHLIST.md"

VALID_TYPES = frozenset({"bug", "feature"})
VALID_STATUSES = frozenset({"open", "reviewing", "in_progress", "done", "rejected"})

STATUS_DE = {
    "open": "Offen",
    "reviewing": "In Prüfung",
    "in_progress": "In Arbeit",
    "done": "Erledigt",
    "rejected": "Abgelehnt",
}
TYPE_DE = {"bug": "Bug", "feature": "Feature-Wunsch"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_raw() -> list[dict[str, Any]]:
    if not WISHLIST_JSON.is_file():
        return []
    try:
        data = json.loads(WISHLIST_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("wishlist load failed: %s", e)
        return []


def _save_raw(items: list[dict[str, Any]]) -> None:
    WISHLIST_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = WISHLIST_JSON.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(WISHLIST_JSON)
    try:
        _write_markdown(items)
    except OSError as e:
        logger.warning("wishlist markdown sync failed: %s", e)


def _esc_cell(s: str) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ").strip()


def _write_markdown(items: list[dict[str, Any]]) -> None:
    rows = []
    for it in items:
        rows.append(
            "| `{id}` | {typ} | {status} | {date} | {by} | {title} |".format(
                id=_esc_cell(it.get("id", ""))[:8],
                typ=TYPE_DE.get(it.get("type", ""), it.get("type", "")),
                status=STATUS_DE.get(it.get("status", ""), it.get("status", "")),
                date=_esc_cell(format_date_display(it.get("created_at"), empty="—")),
                by=_esc_cell(it.get("created_by") or "—"),
                title=_esc_cell(it.get("title") or ""),
            )
        )
    table = "\n".join(rows) if rows else "| — | — | — | — | — | *Noch keine Einträge.* |"

    details = []
    for it in items:
        details.append(
            f"### {it.get('title') or 'Ohne Titel'}\n\n"
            f"- **ID:** `{it.get('id')}`\n"
            f"- **Typ:** {TYPE_DE.get(it.get('type', ''), it.get('type'))}\n"
            f"- **Status:** {STATUS_DE.get(it.get('status', ''), it.get('status'))}\n"
            f"- **Gemeldet:** {format_date_display(it.get('created_at'))} "
            f"von {it.get('created_by') or '—'}\n\n"
            f"{it.get('description') or '_Keine Beschreibung._'}\n"
        )
    details_body = "\n".join(details) if details else "*(Einträge erscheinen hier, sobald Feedback eingereicht wurde.)*\n"

    md = (
        "# Wishlist / Feedback\n\n"
        "Maschinenlesbare Quelle: [`data/wishlist.json`](data/wishlist.json).  \n"
        "Dieses Markdown wird beim Speichern über die App oder Skripte aktualisiert.\n\n"
        "| ID | Typ | Status | Datum | Von | Titel |\n"
        "|----|-----|--------|-------|-----|-------|\n"
        f"{table}\n\n"
        "## Details\n\n"
        f"{details_body}"
    )
    WISHLIST_MD.write_text(md, encoding="utf-8")


def list_items(*, status: str | None = None, type_: str | None = None) -> list[dict[str, Any]]:
    items = _load_raw()
    if status:
        items = [i for i in items if i.get("status") == status]
    if type_:
        items = [i for i in items if i.get("type") == type_]
    items.sort(key=lambda i: i.get("created_at") or "", reverse=True)
    return items


def add_item(
    *,
    title: str,
    description: str,
    type_: str,
    created_by: str | None = None,
    created_by_user_id: int | None = None,
) -> dict[str, Any]:
    typ = (type_ or "").strip().lower()
    if typ not in VALID_TYPES:
        raise ValueError("type muss 'bug' oder 'feature' sein")
    title = (title or "").strip()
    if len(title) < 3:
        raise ValueError("Titel zu kurz")
    if len(title) > 200:
        raise ValueError("Titel zu lang")
    description = (description or "").strip()
    if len(description) > 4000:
        raise ValueError("Beschreibung zu lang")

    item = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": description,
        "type": typ,
        "status": "open",
        "created_at": _now(),
        "created_by": (created_by or "").strip() or None,
        "created_by_user_id": created_by_user_id,
        "updated_at": None,
        "updated_by": None,
    }
    items = _load_raw()
    items.insert(0, item)
    _save_raw(items)
    return item


def update_status(
    item_id: str,
    *,
    status: str,
    updated_by: str | None = None,
) -> dict[str, Any]:
    st = (status or "").strip().lower()
    if st not in VALID_STATUSES:
        raise ValueError(f"Ungültiger Status: {status}")
    items = _load_raw()
    for i, it in enumerate(items):
        if it.get("id") == item_id:
            it = dict(it)
            it["status"] = st
            it["updated_at"] = _now()
            it["updated_by"] = (updated_by or "").strip() or None
            items[i] = it
            _save_raw(items)
            return it
    raise KeyError(item_id)


def done_items_for_changelog() -> list[dict[str, Any]]:
    return [i for i in list_items() if i.get("status") == "done"]


def slug_safe(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())
