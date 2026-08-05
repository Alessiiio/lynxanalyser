"""Admin-only product planning notes (ideas → implementation).

Plain-text board with status, priority, tags, short refs (P-12).
``meta`` remains open for Phase-2 fields (wishlist link, estimate, …).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
PLANNING_JSON = _ROOT / "data" / "planning.json"
PLANNING_MD = _ROOT / "PLANNING.md"

VALID_STATUSES = frozenset({"idea", "planned", "building", "done", "parked"})
VALID_PRIORITIES = frozenset({"low", "med", "high"})

STATUS_DE = {
    "idea": "Idee",
    "planned": "Geplant",
    "building": "In Umsetzung",
    "done": "Umgesetzt",
    "parked": "Zurückgestellt",
}
PRIORITY_DE = {
    "low": "Niedrig",
    "med": "Mittel",
    "high": "Hoch",
}

STATUS_RANK = {"building": 0, "planned": 1, "idea": 2, "parked": 3, "done": 4}
PRIORITY_RANK = {"high": 0, "med": 1, "low": 2}

_TAG_RE = re.compile(r"[^a-z0-9äöüß\-_+]", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_bundle() -> dict[str, Any]:
    """Return {version, next_ref, items}."""
    default: dict[str, Any] = {"version": 1, "next_ref": 1, "items": []}
    if not PLANNING_JSON.is_file():
        return default
    try:
        data = json.loads(PLANNING_JSON.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {"version": 1, "next_ref": _max_ref_n(data) + 1, "items": data}
        if isinstance(data, dict):
            items = data.get("items") if isinstance(data.get("items"), list) else []
            next_ref = data.get("next_ref")
            if not isinstance(next_ref, int) or next_ref < 1:
                next_ref = _max_ref_n(items) + 1
            return {"version": int(data.get("version") or 1), "next_ref": next_ref, "items": items}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("planning load failed: %s", e)
    return default


def _max_ref_n(items: list[dict[str, Any]]) -> int:
    best = 0
    for it in items:
        n = _ref_number(it.get("ref"))
        if n and n > best:
            best = n
    return best


def _ref_number(ref: Any) -> int | None:
    if not ref:
        return None
    m = re.match(r"^P-(\d+)$", str(ref).strip(), re.I)
    return int(m.group(1)) if m else None


def _load_raw() -> list[dict[str, Any]]:
    return list(_load_bundle()["items"])


def _save_bundle(bundle: dict[str, Any]) -> None:
    items = bundle.get("items") if isinstance(bundle.get("items"), list) else []
    # Backfill missing short refs (legacy entries)
    next_ref = int(bundle.get("next_ref") or 1)
    if next_ref < 1:
        next_ref = 1
    changed = False
    for it in items:
        if not it.get("ref"):
            it["ref"] = f"P-{next_ref}"
            next_ref += 1
            changed = True
        it.setdefault("priority", "med")
        if not isinstance(it.get("tags"), list):
            it["tags"] = []
        if not isinstance(it.get("meta"), dict):
            it["meta"] = {}
    if changed:
        bundle["next_ref"] = next_ref

    PLANNING_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "next_ref": int(bundle.get("next_ref") or next_ref),
        "items": items,
    }
    tmp = PLANNING_JSON.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(PLANNING_JSON)
    try:
        _write_markdown(items)
    except OSError as e:
        logger.warning("planning markdown sync failed: %s", e)


def _save_raw(items: list[dict[str, Any]], *, next_ref: int | None = None) -> None:
    bundle = _load_bundle()
    bundle["items"] = items
    if next_ref is not None:
        bundle["next_ref"] = next_ref
    _save_bundle(bundle)


def normalize_priority(priority: str | None, *, default: str = "med") -> str:
    p = (priority or default).strip().lower()
    if p in ("medium", "mittel", "m"):
        p = "med"
    if p in ("niedrig", "l"):
        p = "low"
    if p in ("hoch", "h"):
        p = "high"
    if p not in VALID_PRIORITIES:
        raise ValueError(f"Ungültige Priorität: {priority}")
    return p


def normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    raw: list[str] = []
    if isinstance(tags, str):
        raw = re.split(r"[,;\s]+", tags)
    elif isinstance(tags, list):
        for t in tags:
            raw.extend(re.split(r"[,;\s]+", str(t)))
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in raw:
        slug = _TAG_RE.sub("", t.strip().lower())
        if not slug or len(slug) > 32:
            continue
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
        if len(out) >= 12:
            break
    return out


def _write_markdown(items: list[dict[str, Any]]) -> None:
    lines = [
        "# Planung / Ideen (Admin)",
        "",
        f"Quelle: [`data/planning.json`](data/planning.json) · {len(items)} Einträge",
        "",
    ]
    order = ["building", "planned", "idea", "parked", "done"]
    by_status: dict[str, list[dict]] = {s: [] for s in order}
    for it in items:
        st = it.get("status") if it.get("status") in by_status else "idea"
        by_status[st].append(it)

    for st in order:
        group = by_status[st]
        if not group:
            continue
        lines.append(f"## {STATUS_DE.get(st, st)} ({len(group)})")
        lines.append("")
        for it in group:
            ref = it.get("ref") or "—"
            pri = PRIORITY_DE.get(it.get("priority", ""), it.get("priority") or "—")
            tags = ", ".join(it.get("tags") or []) or "—"
            lines.append(f"### [{ref}] {it.get('title') or 'Ohne Titel'}")
            lines.append("")
            lines.append(f"- **Status:** {STATUS_DE.get(it.get('status', ''), it.get('status'))}")
            lines.append(f"- **Priorität:** {pri}")
            lines.append(f"- **Tags:** {tags}")
            lines.append(f"- **UUID:** `{it.get('id')}`")
            lines.append(f"- **Aktualisiert:** {it.get('updated_at') or it.get('created_at') or '—'}")
            lines.append("")
            body = (it.get("body") or "").strip()
            lines.append(body if body else "_(leer)_")
            lines.append("")
    PLANNING_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def list_items(
    *,
    status: str | None = None,
    priority: str | None = None,
    tag: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    # Ensure short refs exist on read (idempotent migrate)
    bundle = _load_bundle()
    items = list(bundle["items"])
    if any(not it.get("ref") for it in items):
        _save_bundle(bundle)
        items = list(_load_bundle()["items"])

    if status:
        items = [i for i in items if i.get("status") == status]
    if priority:
        pri = normalize_priority(priority)
        items = [i for i in items if (i.get("priority") or "med") == pri]
    if tag:
        want = normalize_tags(tag)
        if want:
            w = want[0]
            items = [i for i in items if w in (i.get("tags") or [])]
    if q and (needle := q.strip().lower()):
        def _matches(it: dict[str, Any]) -> bool:
            blob = " ".join(
                [
                    str(it.get("ref") or ""),
                    str(it.get("title") or ""),
                    str(it.get("body") or ""),
                    " ".join(it.get("tags") or []),
                ]
            ).lower()
            return needle in blob

        items = [i for i in items if _matches(i)]

    # building first → priority high first → newest within group
    items.sort(key=lambda i: i.get("updated_at") or i.get("created_at") or "", reverse=True)
    items.sort(key=lambda i: PRIORITY_RANK.get(str(i.get("priority") or "med"), 9))
    items.sort(key=lambda i: STATUS_RANK.get(str(i.get("status") or "idea"), 9))
    return items


def get_item(item_id: str) -> dict[str, Any] | None:
    for it in _load_raw():
        if it.get("id") == item_id or str(it.get("ref") or "").upper() == str(item_id).upper():
            return it
    return None


def add_item(
    *,
    title: str,
    body: str = "",
    status: str = "idea",
    priority: str = "med",
    tags: Any = None,
    created_by: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = (title or "").strip()
    if len(title) < 2:
        raise ValueError("Titel zu kurz")
    if len(title) > 200:
        raise ValueError("Titel zu lang")
    body = (body or "").strip()
    if len(body) > 20000:
        raise ValueError("Text zu lang (max. 20 000 Zeichen)")
    st = (status or "idea").strip().lower()
    if st not in VALID_STATUSES:
        raise ValueError(f"Ungültiger Status: {status}")
    pri = normalize_priority(priority)
    tag_list = normalize_tags(tags)

    bundle = _load_bundle()
    # backfill before allocating
    _save_bundle(bundle)
    bundle = _load_bundle()
    next_ref = int(bundle.get("next_ref") or 1)
    ref = f"P-{next_ref}"
    now = _now()
    item: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "ref": ref,
        "title": title,
        "body": body,
        "status": st,
        "priority": pri,
        "tags": tag_list,
        "created_at": now,
        "created_by": (created_by or "").strip() or None,
        "updated_at": now,
        "updated_by": (created_by or "").strip() or None,
        "meta": meta if isinstance(meta, dict) else {},
    }
    items = list(bundle["items"])
    items.insert(0, item)
    _save_raw(items, next_ref=next_ref + 1)
    return item


def update_item(
    item_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    tags: Any = None,
    meta: dict[str, Any] | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    items = _load_raw()
    for i, it in enumerate(items):
        if it.get("id") != item_id and str(it.get("ref") or "").upper() != str(item_id).upper():
            continue
        it = dict(it)
        if title is not None:
            t = title.strip()
            if len(t) < 2:
                raise ValueError("Titel zu kurz")
            if len(t) > 200:
                raise ValueError("Titel zu lang")
            it["title"] = t
        if body is not None:
            b = body.strip()
            if len(b) > 20000:
                raise ValueError("Text zu lang (max. 20 000 Zeichen)")
            it["body"] = b
        if status is not None:
            st = status.strip().lower()
            if st not in VALID_STATUSES:
                raise ValueError(f"Ungültiger Status: {status}")
            it["status"] = st
        if priority is not None:
            it["priority"] = normalize_priority(priority)
        if tags is not None:
            it["tags"] = normalize_tags(tags)
        if meta is not None:
            if not isinstance(meta, dict):
                raise ValueError("meta muss ein Objekt sein")
            prev = it.get("meta") if isinstance(it.get("meta"), dict) else {}
            it["meta"] = {**prev, **meta}
        if not it.get("ref"):
            bundle = _load_bundle()
            n = int(bundle.get("next_ref") or 1)
            it["ref"] = f"P-{n}"
            bundle["next_ref"] = n + 1
            items[i] = it
            bundle["items"] = items
            it["updated_at"] = _now()
            it["updated_by"] = (updated_by or "").strip() or None
            _save_bundle(bundle)
            return it
        it["updated_at"] = _now()
        it["updated_by"] = (updated_by or "").strip() or None
        items[i] = it
        _save_raw(items)
        return it
    raise KeyError(item_id)


def delete_item(item_id: str) -> None:
    items = _load_raw()
    new = [
        it
        for it in items
        if it.get("id") != item_id and str(it.get("ref") or "").upper() != str(item_id).upper()
    ]
    if len(new) == len(items):
        raise KeyError(item_id)
    _save_raw(new)
