"""Multi-level fraud company network (Firma ↔ Personen ↔ weitere Firmen)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from typing import Any

import config
from app.checks.zefix_check import _format_uid, _is_active, _zefix_get, _zefix_search
from app.checks.zefix_mutations import _strip_ft_tags
from app.hr_network.company_cases import list_confirmed_case_seeds
from app.hr_network.moneyhouse_person import (
    firm_names_match as firm_core_names_match,
    human_identity_note,
    mh_profile_url,
    moneyhouse_person_search_enabled,
    search_person_mandates,
)
from app.hr_network.person_search import search_persons_batch
from app.hr_network.person_names import (
    merge_role_lists,
    names_same_person,
    prefer_display_name,
)
from app.hr_network.shab_parser import (
    build_person_timeline,
    collect_persons_from_publications,
    enrich_publication_for_timeline,
    infer_person_gender,
)
from app.hr_network.service import _format_address, _legal_form_label, care_of_display_name
from app.hr_network.zefix_resolve import format_company_uid, resolve_company_detail, uid_digits

logger = logging.getLogger(__name__)

# Level meanings (min_level on nodes/edges):
# 1 — Seed firms + current owners
# 2 — + former owners + Zefix structural company links
# 3 — + other firms of current owners (Zefix/SHAB first, Moneyhouse fill-in)
# 4 — + other firms of former owners
# 5 — + persons at those related firms (2nd ring)

_RELATION_FIELDS = (
    ("hasTakenOver", "Hat übernommen"),
    ("wasTakenOverBy", "Wurde übernommen von"),
    ("branchOffices", "Zweigniederlassung"),
    ("headOffices", "Hauptsitz"),
    ("furtherHeadOffices", "Weiterer Hauptsitz"),
    ("auditCompanies", "Revisionsstelle"),
)

LEVEL_LABELS = {
    1: "Firma + aktuelle Organe",
    2: "+ ehemalige Organe + Struktur",
    3: "Mandate der aktuellen Organe",
    4: "+ Mandate der ehemaligen",
    5: "+ Personen an Mandatsfirmen (2. Ring)",
}


def _company_id(ehraid: Any) -> str:
    return f"company:{ehraid}"


def _person_id(pid: str) -> str:
    return f"person:{pid}"


def _uid_digits(uid: str | None) -> str:
    return uid_digits(uid)


async def _resolve_detail(name: str | None, uid: str | None) -> dict:
    return await resolve_company_detail(name, uid)


def _company_summary(detail: dict, *, seed: bool = False) -> dict[str, Any]:
    uid_raw = str(detail.get("uid") or "")
    status = detail.get("status")
    status_key = ""
    if isinstance(status, dict):
        status_key = str(status.get("id") or status.get("key") or status.get("shortDescription") or "")
    else:
        status_key = str(status or "")
    return {
        "name": detail.get("name"),
        "ehraid": detail.get("ehraid"),
        "uid": _format_uid(uid_raw) if uid_raw else None,
        "status": status_key or status,
        "deletion_date": detail.get("deletionDate") or detail.get("deleteDate"),
        "legal_form": _legal_form_label(detail.get("legalForm")),
        "canton": detail.get("canton"),
        "registry_office_id": detail.get("registryOfCommerceId"),
        "legal_seat": detail.get("legalSeat"),
        "address": _format_address(detail.get("address")),
        "is_seed": seed,
        "cantonal_excerpt_url": detail.get("cantonalExcerptWeb"),
        "purpose_short": (
            _strip_ft_tags(detail.get("purpose", ""))[:200] if detail.get("purpose") else None
        ),
    }


_PERSON_EDGE_TYPES = frozenset({"person_role", "person_company"})
_GENERIC_EDGE_LABELS = frozenset(
    {
        "mandat",
        "erwähnt",
        "shab-treffer",
        "aktuell",
        "ehemalig",
        "eingetragen",
        "alte firma",
    }
)


def _normalize_edge_label_part(part: str) -> str:
    s = (part or "").strip()
    s = re.sub(r"\s*·\s*(?:ehemalig|alte firma)\s*$", "", s, flags=re.I).strip()
    return s


def _split_edge_label(label: str | None) -> list[str]:
    raw = (label or "").strip()
    if not raw:
        return []
    parts = re.split(r"\s*[,;]\s*|\s*·\s*", raw)
    out: list[str] = []
    for p in parts:
        n = _normalize_edge_label_part(p)
        if n:
            out.append(n)
    return out


def merge_edge_labels(a: str | None, b: str | None) -> str:
    """Merge role labels; prefer concrete roles over generic «Mandat»."""
    concrete: list[str] = []
    generic: list[str] = []
    seen: set[str] = set()
    for part in _split_edge_label(a) + _split_edge_label(b):
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        if key in _GENERIC_EDGE_LABELS:
            generic.append(part)
        else:
            concrete.append(part)
    if concrete:
        return ", ".join(concrete)
    if generic:
        return generic[0]
    return (a or b or "Mandat").strip()


class _GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._edge_keys: set[tuple] = set()
        # person node id aliases when middle-name variants collapse onto one node
        self._person_id_alias: dict[str, str] = {}

    def resolve_id(self, nid: str) -> str:
        """Follow person-id alias chain after middle-name merges."""
        seen: set[str] = set()
        while nid in self._person_id_alias and nid not in seen:
            seen.add(nid)
            nid = self._person_id_alias[nid]
        return nid

    def _find_same_person_node(self, label: str | None) -> dict[str, Any] | None:
        if not label:
            return None
        for node in self.nodes.values():
            if node.get("type") != "person":
                continue
            other = node.get("label") or node.get("name")
            if names_same_person(label, other):
                return node
        return None

    def _merge_person_fields(self, keep: dict[str, Any], node: dict[str, Any]) -> None:
        keep["min_level"] = min(
            keep.get("min_level", 5),
            node.get("min_level", 5),
        )
        if node.get("is_seed"):
            keep["is_seed"] = True
        if node.get("is_center"):
            keep["is_center"] = True
        # Never promote former → current. Same person can be former at the
        # seed and current at a related firm (level 5); seed-centric status
        # must stay "former" so they are not shown as active seed officers.
        if keep.get("person_status") != "former" and node.get("person_status") == "current":
            keep["person_status"] = "current"
        elif not keep.get("person_status") and node.get("person_status"):
            keep["person_status"] = node["person_status"]

        # Prefer fuller SHAB name (with middle) when it is the same identity
        preferred = prefer_display_name(
            keep.get("label"),
            node.get("label") or node.get("name"),
        )
        if preferred:
            keep["label"] = preferred
        keep["roles"] = merge_role_lists(keep.get("roles"), node.get("roles"))

        for key in (
            "uid",
            "residence",
            "nationality",
            "heimatort",
            "first_seen",
            "last_seen",
            "exited_date",
            "gender",
            "moneyhouse_identity_status",
            "identity_warning",
            "case_flag_label",
            "watch_status",
            "watched_person_id",
        ):
            if node.get(key) and not keep.get(key):
                keep[key] = node[key]
        if node.get("moneyhouse_seed_confirmed"):
            keep["moneyhouse_seed_confirmed"] = True
        if node.get("case_involved"):
            keep["case_involved"] = True
        if node.get("on_watchlist"):
            keep["on_watchlist"] = True
        if node.get("source") and not keep.get("source"):
            keep["source"] = node["source"]
        # Merge mandate lists when person nodes collapse (middle-name aliases)
        for m in node.get("mandates") or []:
            if not isinstance(m, dict):
                continue
            self.record_person_mandate(
                keep.get("id") or "",
                company_name=m.get("company"),
                company_uid=m.get("uid"),
                ehraid=m.get("ehraid"),
                status=m.get("status"),
            )

    def add_node(self, node: dict[str, Any]) -> None:
        nid = node["id"]
        if node.get("type") == "person":
            nid = self.resolve_id(nid)
            node = {**node, "id": nid}
            existing = self.nodes.get(nid)
            if existing and existing.get("type") == "person":
                self._merge_person_fields(existing, node)
                return
            same = self._find_same_person_node(node.get("label") or node.get("name"))
            if same and same.get("id") != nid:
                # Collapse middle-name variant onto the earlier person node
                self._person_id_alias[nid] = same["id"]
                self._merge_person_fields(same, node)
                return

        existing = self.nodes.get(nid)
        if existing:
            # Keep lowest min_level (visible earlier when zooming in)
            existing["min_level"] = min(
                existing.get("min_level", 5),
                node.get("min_level", 5),
            )
            if node.get("is_seed"):
                existing["is_seed"] = True
            if node.get("is_center"):
                existing["is_center"] = True
            if existing.get("type") == "person":
                self._merge_person_fields(existing, node)
                return
            for key in ("roles", "label", "uid", "residence", "nationality", "heimatort"):
                if node.get(key) and not existing.get(key):
                    existing[key] = node[key]
            return
        self.nodes[nid] = node

    def _find_person_company_edge(self, frm: str, to: str) -> dict[str, Any] | None:
        """One undirected person↔company edge regardless of role label / type."""
        ends = {frm, to}
        for e in self.edges:
            if e.get("type") not in _PERSON_EDGE_TYPES:
                continue
            if {e.get("from"), e.get("to")} == ends:
                return e
        return None

    def add_edge(
        self,
        *,
        frm: str,
        to: str,
        label: str,
        edge_type: str,
        min_level: int,
        person_status: str | None = None,
    ) -> None:
        frm = self.resolve_id(frm)
        to = self.resolve_id(to)

        # Person↔firm: single edge per pair; merge role labels (no Mandat + GF parallel).
        if edge_type in _PERSON_EDGE_TYPES:
            existing = self._find_person_company_edge(frm, to)
            if existing is not None:
                existing["label"] = merge_edge_labels(existing.get("label"), label)
                existing["min_level"] = min(
                    existing.get("min_level", 5), min_level
                )
                if person_status == "former" or (
                    person_status in ("current", "former")
                    and not existing.get("person_status")
                ):
                    existing["person_status"] = person_status
                # Prefer person_role when either side carries concrete roles
                if edge_type == "person_role" or existing.get("type") == "person_role":
                    existing["type"] = "person_role"
                return
            edge: dict[str, Any] = {
                "from": frm,
                "to": to,
                "label": label,
                "type": edge_type,
                "min_level": min_level,
            }
            if person_status in ("current", "former"):
                edge["person_status"] = person_status
            self.edges.append(edge)
            # Track for company_link-style exact keys too (best-effort)
            self._edge_keys.add((frm, to, "person_link", edge_type))
            return

        key = (frm, to, label, edge_type)
        if key in self._edge_keys:
            for e in self.edges:
                if (
                    e.get("from") == frm
                    and e.get("to") == to
                    and e.get("label") == label
                    and e.get("type") == edge_type
                ):
                    e["min_level"] = min(e.get("min_level", 5), min_level)
                    break
            return
        self._edge_keys.add(key)
        edge = {
            "from": frm,
            "to": to,
            "label": label,
            "type": edge_type,
            "min_level": min_level,
        }
        self.edges.append(edge)

    def record_person_mandate(
        self,
        person_nid: str,
        *,
        company_name: str | None,
        company_uid: str | None = None,
        ehraid: int | None = None,
        status: str | None = None,
    ) -> None:
        """Append/update per-person mandate list (all known firms, incl. seed)."""
        nid = self.resolve_id(person_nid)
        node = self.nodes.get(nid)
        if not node or node.get("type") != "person":
            return
        mandates: list[dict[str, Any]] = list(node.get("mandates") or [])
        st = status if status in ("current", "former") else None
        dig = _uid_digits(company_uid) if company_uid else ""
        label = (company_name or "").strip()
        for m in mandates:
            same = False
            if ehraid is not None and m.get("ehraid") is not None:
                same = int(m["ehraid"]) == int(ehraid)
            elif dig and _uid_digits(m.get("uid")):
                same = _uid_digits(m.get("uid")) == dig
            elif label and firm_core_names_match(label, m.get("company")):
                same = True
            if not same:
                continue
            if st == "former" or not m.get("status"):
                if st:
                    m["status"] = st
            if company_uid and not m.get("uid"):
                m["uid"] = company_uid
            if ehraid is not None and m.get("ehraid") is None:
                m["ehraid"] = ehraid
            if label and not m.get("company"):
                m["company"] = label
            node["mandates"] = mandates
            return
        entry: dict[str, Any] = {}
        if label:
            entry["company"] = label
        if company_uid:
            entry["uid"] = company_uid
        if ehraid is not None:
            entry["ehraid"] = ehraid
        if st:
            entry["status"] = st
        if entry:
            mandates.append(entry)
            node["mandates"] = mandates

    def export(self, level: int) -> tuple[list[dict], list[dict]]:
        nodes = [n for n in self.nodes.values() if n.get("min_level", 1) <= level]
        node_ids = {n["id"] for n in nodes}
        # Resolve any alias ids still stored on edges
        for e in self.edges:
            e["from"] = self.resolve_id(e["from"])
            e["to"] = self.resolve_id(e["to"])
        edges = [
            e for e in self.edges
            if e.get("min_level", 1) <= level
            and e["from"] in node_ids
            and e["to"] in node_ids
        ]
        # Safety net: collapse any residual person↔firm duplicates
        edges = self._dedupe_exported_person_edges(edges)
        return nodes, edges

    @staticmethod
    def _dedupe_exported_person_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        person_idx: dict[frozenset[str], int] = {}
        for e in edges:
            if e.get("type") not in _PERSON_EDGE_TYPES:
                kept.append(e)
                continue
            key = frozenset({e.get("from") or "", e.get("to") or ""})
            if key in person_idx:
                prev = kept[person_idx[key]]
                prev["label"] = merge_edge_labels(prev.get("label"), e.get("label"))
                prev["min_level"] = min(
                    prev.get("min_level", 5), e.get("min_level", 5)
                )
                if e.get("person_status") == "former" or not prev.get("person_status"):
                    if e.get("person_status") in ("current", "former"):
                        prev["person_status"] = e["person_status"]
                if e.get("type") == "person_role" or prev.get("type") == "person_role":
                    prev["type"] = "person_role"
                continue
            person_idx[key] = len(kept)
            kept.append(e)
        return kept


def _normalize_identity_overrides(
    raw: list[dict[str, Any]] | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Normalize identity lock/ignore requests for Moneyhouse expansion."""
    if not raw:
        return []
    items: list[dict[str, Any]]
    if isinstance(raw, dict):
        items = [raw]
    else:
        items = [x for x in raw if isinstance(x, dict)]
    out: list[dict[str, Any]] = []
    for item in items:
        action = str(item.get("action") or "accept").strip().lower()
        if action not in ("accept", "ignore", "force"):
            action = "accept"
        if action == "force":
            action = "accept"
        person_name = (item.get("person_name") or item.get("name") or "").strip()
        person_id = (item.get("person_id") or item.get("person_graph_id") or "").strip()
        if person_id.startswith("person:"):
            person_id = person_id[len("person:") :]
        mh_key = (
            item.get("moneyhouse_person_key")
            or item.get("mh_person_key")
            or item.get("person_key")
            or item.get("uri")
            or ""
        )
        mh_key = str(mh_key).strip()
        if not person_name and not person_id:
            continue
        if action == "accept" and not mh_key:
            continue
        out.append(
            {
                "action": action,
                "person_name": person_name,
                "person_id": person_id,
                "moneyhouse_person_key": mh_key,
            }
        )
    return out


def _override_for_person(
    overrides: list[dict[str, Any]],
    *,
    pid: str,
    person_name: str | None,
) -> dict[str, Any] | None:
    if not overrides:
        return None
    name = (person_name or "").strip()
    for ov in overrides:
        oid = (ov.get("person_id") or "").strip()
        if oid and (oid == pid or names_same_person(oid, pid)):
            return ov
        oname = (ov.get("person_name") or "").strip()
        if oname and name and names_same_person(oname, name):
            return ov
    return None


def _norm_person_token(value: str | None) -> str:
    s = str(value or "").strip()
    if s.lower().startswith("person:"):
        s = s[len("person:") :].strip()
    return s


def _person_identity_matches(
    *,
    candidate_name: str | None,
    candidate_id: str | None,
    person_name: str | None,
    person_id: str | None,
) -> bool:
    cid = _norm_person_token(candidate_id)
    pid = _norm_person_token(person_id)
    if cid and pid and (cid == pid or names_same_person(cid, pid)):
        return True
    cn = (candidate_name or "").strip()
    pn = (person_name or "").strip()
    return bool(cn and pn and names_same_person(cn, pn))


def _strip_identity_ui_for_person(
    person_search: dict[str, Any],
    *,
    person_name: str | None,
    person_id: str | None,
) -> None:
    """Drop picker rows / warnings for a person the user just resolved."""
    choices = list(person_search.get("identity_choices") or [])
    person_search["identity_choices"] = [
        c
        for c in choices
        if isinstance(c, dict)
        and not _person_identity_matches(
            candidate_name=c.get("person_name"),
            candidate_id=c.get("person_id") or c.get("person_graph_id"),
            person_name=person_name,
            person_id=person_id,
        )
    ]
    name_l = (person_name or "").strip().lower()
    pid_l = _norm_person_token(person_id).lower()
    kept_warns: list[str] = []
    for w in person_search.get("identity_warnings") or []:
        line = str(w or "").strip()
        if not line:
            continue
        lower = line.lower()
        if name_l and lower.startswith(name_l):
            continue
        if pid_l and pid_l in lower:
            continue
        kept_warns.append(line)
    person_search["identity_warnings"] = kept_warns


def _hydrate_graph_builder(payload: dict[str, Any]) -> _GraphBuilder:
    """Rebuild a mutable graph from a prior analysis payload (cache or client)."""
    graph = _GraphBuilder()
    for raw in payload.get("nodes") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        node = dict(raw)
        if isinstance(node.get("mandates"), list):
            node["mandates"] = [
                dict(m) for m in node["mandates"] if isinstance(m, dict)
            ]
        graph.nodes[str(node["id"])] = node
    for raw in payload.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        edge = dict(raw)
        frm = edge.get("from")
        to = edge.get("to")
        if not frm or not to:
            continue
        graph.edges.append(edge)
        et = edge.get("type") or ""
        if et in _PERSON_EDGE_TYPES:
            graph._edge_keys.add((frm, to, "person_link", et))
        else:
            graph._edge_keys.add((frm, to, edge.get("label"), et))
    return graph


def _person_meta_from_base(
    base: dict[str, Any],
    *,
    person_name: str | None,
    person_id: str | None,
) -> dict[str, Any]:
    """Locate organ context (seed firm, residence, graph id) in a stored analysis."""
    table = base.get("persons_table") or []
    for entry in table:
        if not isinstance(entry, dict):
            continue
        if _person_identity_matches(
            candidate_name=entry.get("name") or entry.get("person_name"),
            candidate_id=entry.get("person_id") or entry.get("id"),
            person_name=person_name,
            person_id=person_id,
        ):
            pid = _norm_person_token(entry.get("person_id") or entry.get("id"))
            return {
                "pid": pid or _norm_person_token(person_id),
                "name": (entry.get("name") or person_name or "").strip(),
                "residence": entry.get("residence"),
                "seed_company": entry.get("seed_company"),
                "seed_uid": entry.get("seed_uid"),
                "min_level": 3 if (entry.get("status") or "current") == "current" else 4,
            }

    for node in base.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "person":
            continue
        nid = str(node.get("id") or "")
        if _person_identity_matches(
            candidate_name=node.get("label") or node.get("name"),
            candidate_id=nid,
            person_name=person_name,
            person_id=person_id,
        ):
            pid = _norm_person_token(nid) or _norm_person_token(person_id)
            return {
                "pid": pid,
                "name": (
                    node.get("label") or node.get("name") or person_name or ""
                ).strip(),
                "residence": node.get("residence"),
                "seed_company": None,
                "seed_uid": None,
                "min_level": int(node.get("min_level") or 3),
            }

    # Fall back to request identity only
    pid = _norm_person_token(person_id)
    return {
        "pid": pid,
        "name": (person_name or "").strip(),
        "residence": None,
        "seed_company": None,
        "seed_uid": None,
        "min_level": 3,
    }


def _seed_sets_from_base(base: dict[str, Any]) -> tuple[set[str], set[int]]:
    seed_uids: set[str] = set()
    seed_ehraids: set[int] = set()
    for s in base.get("seed_companies") or []:
        if not isinstance(s, dict):
            continue
        dig = _uid_digits(s.get("uid"))
        if dig:
            seed_uids.add(dig)
        if s.get("ehraid") is not None:
            try:
                seed_ehraids.add(int(s["ehraid"]))
            except (TypeError, ValueError):
                pass
    for n in base.get("nodes") or []:
        if not isinstance(n, dict) or n.get("type") != "company":
            continue
        if not n.get("is_seed"):
            continue
        dig = _uid_digits(n.get("uid"))
        if dig:
            seed_uids.add(dig)
        if n.get("ehraid") is not None:
            try:
                seed_ehraids.add(int(n["ehraid"]))
            except (TypeError, ValueError):
                pass
    return seed_uids, seed_ehraids


def _status_key_simple(status: Any) -> Any:
    if isinstance(status, dict):
        return (
            status.get("id")
            or status.get("key")
            or status.get("shortDescription")
            or status
        )
    return status


async def apply_identity_confirmation(
    *,
    base: dict[str, Any],
    level: int = 3,
    person_name: str | None = None,
    person_id: str | None = None,
    moneyhouse_person_key: str | None = None,
    action: str = "accept",
) -> dict[str, Any]:
    """
    Apply a single Moneyhouse identity accept/ignore to an existing analysis graph.

    Does **not** re-run SHAB / multi-person SW5 walks. On accept, expands only the
    confirmed person via Moneyhouse→Zefix and merges new companies/edges into
    ``base``. Shared cache payloads should remain identity-free; this produces a
    session-level graph delta.
    """
    import copy

    if not isinstance(base, dict) or "nodes" not in base:
        raise ValueError("base analysis (nodes) fehlt")

    action = (action or "accept").strip().lower()
    if action not in ("accept", "ignore"):
        raise ValueError("action muss accept oder ignore sein")
    if action == "accept" and not (moneyhouse_person_key or "").strip():
        raise ValueError("moneyhouse_person_key erforderlich zum Übernehmen")
    if not (person_name or person_id):
        raise ValueError("person_name oder person_id erforderlich")

    level = max(1, min(5, int(level)))
    out: dict[str, Any] = copy.deepcopy(base)
    out["level"] = level
    if level in LEVEL_LABELS:
        out["level_label"] = LEVEL_LABELS[level]
    out.setdefault("level_labels", LEVEL_LABELS)

    stats = out.setdefault("stats", {})
    if not isinstance(stats, dict):
        stats = {}
        out["stats"] = stats
    ps = stats.get("person_search")
    if not isinstance(ps, dict):
        ps = {}
        stats["person_search"] = ps
    else:
        # shallow copy so later mutation does not alias nested shared structure oddly
        ps = dict(ps)
        stats["person_search"] = ps

    _strip_identity_ui_for_person(
        ps, person_name=person_name, person_id=person_id
    )

    meta = _person_meta_from_base(
        out, person_name=person_name, person_id=person_id
    )
    pid = (meta.get("pid") or "").strip()
    display_name = (meta.get("name") or person_name or "").strip()
    p_nid = _person_id(pid) if pid else None

    # Clear soft flags on the person node when user resolves identity
    for node in out.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "person":
            continue
        if not _person_identity_matches(
            candidate_name=node.get("label") or node.get("name"),
            candidate_id=node.get("id"),
            person_name=display_name or person_name,
            person_id=pid or person_id,
        ):
            continue
        if action == "ignore":
            node["moneyhouse_identity_status"] = "ignored"
            node.pop("identity_warning", None)
            node["moneyhouse_seed_confirmed"] = False
        else:
            node["moneyhouse_identity_status"] = "forced"
            node["moneyhouse_seed_confirmed"] = True
            node.pop("identity_warning", None)
        p_nid = str(node.get("id") or p_nid)

    out["identity_action"] = action
    out["identity_confirmed"] = action == "accept"
    out["incremental_identity"] = True
    # Session patch on top of a (often cached) base — not a fresh full scan store key.
    out["cached"] = False
    out["force_refresh"] = False

    if action == "ignore":
        ps["note"] = (
            (ps.get("note") or "")
            + f" Moneyhouse für «{display_name or 'Person'}» ignoriert (ohne Neu-Scan)."
        ).strip()
        try:
            from app.hr_network.case_flags import annotate_network_with_case_flags

            out = await annotate_network_with_case_flags(out)
        except Exception as e:
            logger.debug("Case-flag annotation skipped (ignore identity): %s", e)
        return out

    if not moneyhouse_person_search_enabled():
        ps["note"] = (
            (ps.get("note") or "")
            + " Moneyhouse deaktiviert — Identität gespeichert, keine Firmen-Nachzug."
        ).strip()
        return out

    if not display_name:
        raise ValueError("Person name fehlt für Moneyhouse-Expansion")

    force_key = (moneyhouse_person_key or "").strip()
    try:
        mh = await asyncio.to_thread(
            search_person_mandates,
            display_name,
            residence=meta.get("residence"),
            seed_company=meta.get("seed_company"),
            seed_uid=meta.get("seed_uid"),
            force_mh_person_key=force_key,
        )
    except Exception as e:
        logger.warning(
            "Incremental Moneyhouse expand failed for %r: %s", display_name, e
        )
        ps["note"] = (
            (ps.get("note") or "")
            + f" Moneyhouse-Nachzug für «{display_name}» fehlgeschlagen."
        ).strip()
        return out

    ps["moneyhouse_persons"] = int(ps.get("moneyhouse_persons") or 0) + 1
    if not mh.get("matched_person"):
        logger.warning(
            "Moneyhouse force key %r not resolved for %r — identity locked without firms",
            force_key,
            display_name,
        )
        ps["note"] = (
            (ps.get("note") or "")
            + f" Profil für «{display_name}» gesperrt, aber Moneyhouse lieferte kein Profil."
        ).strip()
        return out

    if not pid:
        # Create a stable-ish person id from graph node if possible
        for node in out.get("nodes") or []:
            if isinstance(node, dict) and node.get("type") == "person":
                if names_same_person(
                    display_name, node.get("label") or node.get("name")
                ):
                    pid = _norm_person_token(node.get("id"))
                    p_nid = str(node.get("id"))
                    break
    if not pid:
        pid = display_name
        p_nid = _person_id(pid)

    graph = _hydrate_graph_builder(out)
    seed_uids, seed_ehraids = _seed_sets_from_base(out)
    related_before = {
        int(n["ehraid"])
        for n in graph.nodes.values()
        if n.get("type") == "company"
        and not n.get("is_seed")
        and n.get("ehraid") is not None
    }

    # Ensure person node exists
    if p_nid not in graph.nodes:
        graph.add_node(
            {
                "id": p_nid,
                "type": "person",
                "label": display_name,
                "min_level": int(meta.get("min_level") or 3),
                "moneyhouse_identity_status": "forced",
                "moneyhouse_seed_confirmed": True,
            }
        )
    else:
        node = graph.nodes[graph.resolve_id(p_nid)]
        node["moneyhouse_identity_status"] = "forced"
        node["moneyhouse_seed_confirmed"] = True
        node.pop("identity_warning", None)

    min_level = int(meta.get("min_level") or 3)
    if min_level < 3:
        min_level = 3
    mh_added = 0
    source_tag = "moneyhouse+zefix+forced"

    for company in mh.get("companies") or []:
        cname = (company.get("name") or "").strip()
        if not cname:
            continue
        try:
            detail = await resolve_company_detail(cname, None)
        except Exception as e:
            logger.info(
                "Zefix resolve failed for Moneyhouse firm %r (incremental): %s",
                cname,
                e,
            )
            continue
        if not isinstance(detail, dict):
            continue
        ehraid = detail.get("ehraid")
        if not ehraid:
            continue
        try:
            ehraid_i = int(ehraid)
        except (TypeError, ValueError):
            continue
        uid = format_company_uid(detail) or detail.get("uid")
        m_uid = _uid_digits(uid)
        is_seed = (m_uid and m_uid in seed_uids) or (ehraid_i in seed_ehraids)

        if is_seed:
            graph.record_person_mandate(
                p_nid,
                company_name=detail.get("name") or cname,
                company_uid=uid,
                ehraid=ehraid_i,
                status="current",
            )
            continue

        already = ehraid_i in related_before or _company_id(ehraid_i) in graph.nodes
        c_nid = _company_id(ehraid_i)
        graph.add_node(
            {
                "id": c_nid,
                "type": "company",
                "label": detail.get("name") or cname,
                "ehraid": ehraid_i,
                "uid": uid,
                "status": _status_key_simple(detail.get("status")),
                "legal_seat": detail.get("legalSeat"),
                "role_hint": source_tag,
                "person_linked_at": company.get("from"),
                "mandate_source": source_tag,
                "is_seed": False,
                "min_level": min_level,
            }
        )
        graph.add_edge(
            frm=p_nid,
            to=c_nid,
            label="Mandat",
            edge_type="person_company",
            min_level=min_level,
            person_status="current",
        )
        graph.record_person_mandate(
            p_nid,
            company_name=detail.get("name") or cname,
            company_uid=uid,
            ehraid=ehraid_i,
            status="current",
        )
        if not already:
            related_before.add(ehraid_i)
            mh_added += 1

    nodes, edges = graph.export(level)
    out["nodes"] = nodes
    out["edges"] = edges
    stats["node_count"] = len(nodes)
    stats["edge_count"] = len(edges)

    # Sync mandates into persons_table sidebar
    resolved_p = graph.resolve_id(p_nid)
    person_node = graph.nodes.get(resolved_p) or {}
    mandates = list(person_node.get("mandates") or [])
    table = list(out.get("persons_table") or [])
    updated_table = False
    for i, entry in enumerate(table):
        if not isinstance(entry, dict):
            continue
        if _person_identity_matches(
            candidate_name=entry.get("name") or entry.get("person_name"),
            candidate_id=entry.get("person_id") or entry.get("id"),
            person_name=display_name or person_name,
            person_id=pid or person_id,
        ):
            table[i] = {**entry, "mandates": mandates}
            updated_table = True
            break
    if updated_table:
        out["persons_table"] = table

    ps["moneyhouse_matches"] = int(ps.get("moneyhouse_matches") or 0) + mh_added
    ps["matches"] = int(ps.get("matches") or 0) + mh_added
    ps["moneyhouse_seed_confirmed"] = (
        int(ps.get("moneyhouse_seed_confirmed") or 0) + 1
    )
    out["identity_firms_added"] = mh_added
    if mh_added:
        ps["note"] = (
            f"Identität «{display_name}» übernommen · "
            f"{mh_added} Firmen via Moneyhouse→Zefix (ohne vollen Neu-Scan)."
        )
    else:
        ps["note"] = (
            f"Identität «{display_name}» übernommen · "
            "keine neuen Firmen (bereits im Graph oder Zefix ohne Treffer)."
        )

    try:
        from app.hr_network.case_flags import annotate_network_with_case_flags

        out = await annotate_network_with_case_flags(out)
    except Exception as e:
        logger.debug("Case-flag annotation skipped (confirm identity): %s", e)
    return out


async def build_fraud_network(
    *,
    level: int = 2,
    company_ids: list[str] | None = None,
    ad_hoc_company: dict[str, str] | None = None,
    max_person_searches: int = 8,
    identity_overrides: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build multi-level network from fraud list and/or a single ad-hoc company.

    Levels 1–2 use Zefix/SHAB org data only (fast).
    Levels 3–5 expand person→company mandates (Zefix/SHAB primary,
    Moneyhouse optional fill-in only).

    If ``ad_hoc_company`` is set (``{"name": ..., "uid": ...}``), that firm is
    the (additional) seed — enabling single-company deep analysis without a
    prior list entry. When only ``ad_hoc_company`` is provided (no company_ids
    and empty fraud list is OK), analysis runs on that seed alone.

    ``identity_overrides`` locks or skips Moneyhouse person identity for named
    organ persons (accept with moneyhouse_person_key, or ignore).
    """
    # Offline demo firm — short-circuit before Zefix credentials / live APIs.
    if ad_hoc_company and (ad_hoc_company.get("name") or ad_hoc_company.get("uid")):
        try:
            from app.hr_network.demo_fixture import (
                DemoFixtureError,
                build_demo_fraud_network,
                is_demo_request,
            )

            if is_demo_request(
                name=(ad_hoc_company.get("name") or None),
                uid=(ad_hoc_company.get("uid") or None),
            ):
                return build_demo_fraud_network(level=level)
        except DemoFixtureError:
            raise
        except Exception:
            pass

    if not config.ZEFIX_USERNAME or not config.ZEFIX_PASSWORD:
        raise PermissionError("Zefix-Zugangsdaten fehlen")

    level = max(1, min(5, int(level)))
    identity_override_list = _normalize_identity_overrides(identity_overrides)

    entries: list[dict[str, Any]] = []
    if ad_hoc_company and (ad_hoc_company.get("name") or ad_hoc_company.get("uid")):
        entries.append({
            "id": "ad-hoc",
            "name": (ad_hoc_company.get("name") or "").strip() or None,
            "uid": (ad_hoc_company.get("uid") or "").strip() or None,
            "note": "",
            "category": "ad-hoc",
        })
    else:
        entries = await list_confirmed_case_seeds()
        if company_ids:
            id_set = set(company_ids)
            entries = [e for e in entries if e.get("id") in id_set]

    if not entries:
        raise ValueError("Keine Firma angegeben — Suche starten oder bestätigte Fälle öffnen")

    graph = _GraphBuilder()
    seed_summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seed_uids: set[str] = set()
    seed_ehraids: set[int] = set()
    # person_id -> meta for later expansion
    current_persons: dict[str, dict[str, Any]] = {}
    former_persons: dict[str, dict[str, Any]] = {}
    person_registry: dict[str, int | None] = {}  # person_id -> preferred registry office

    # ── Resolve seed companies (Level 1–2 base) ─────────────────────────
    for entry in entries:
        try:
            detail = await _resolve_detail(entry.get("name"), entry.get("uid"))
        except Exception as e:
            errors.append({
                "entry_id": entry.get("id", ""),
                "query": entry.get("name") or entry.get("uid") or "",
                "error": str(e)[:160],
            })
            continue

        summary = _company_summary(detail, seed=True)
        summary["list_entry_id"] = entry.get("id")
        summary["note"] = entry.get("note")
        summary["category"] = entry.get("category")
        # Enrich for company-analysis card / HR wrapper compatibility
        from app.checks.zefix_mutations import analyze_mutations, _label_for_key, _strip_ft_tags as _strip
        from app.hr_network.shab_parser import detect_shab_warnings

        mutation_info = analyze_mutations(
            detail.get("sogcPub"),
            old_names=detail.get("oldNames"),
            has_taken_over=detail.get("hasTakenOver"),
            was_taken_over_by=detail.get("wasTakenOverBy"),
        )
        warnings = list(mutation_info.get("warning_flags") or [])
        warnings.extend(detect_shab_warnings(detail.get("sogcPub"))
        )
        summary["warnings"] = list(dict.fromkeys(warnings))
        summary["mutation_analysis"] = mutation_info.get("mutation_analysis")
        summary["publication_count"] = mutation_info.get("publication_count", 0)
        pubs = sorted(
            [p for p in (detail.get("sogcPub") or []) if isinstance(p, dict)],
            key=lambda p: p.get("sogcDate") or "",
            reverse=True,
        )
        summary["recent_publications"] = []
        for pub in pubs[:60]:
            keys = [
                t.get("key", "")
                for t in (pub.get("mutationTypes") or [])
                if isinstance(t, dict)
            ]
            types_de = [
                _label_for_key(t.get("key", ""))
                for t in (pub.get("mutationTypes") or [])
                if isinstance(t, dict) and t.get("key")
            ]
            enrich = enrich_publication_for_timeline(pub)
            msg_full = enrich["message_clean"] or _strip(pub.get("message", ""))
            summary["recent_publications"].append(
                {
                    "date": pub.get("sogcDate"),
                    "types": keys,
                    "types_de": types_de,
                    # Structured first — UI uses this instead of full SHAB wall of text
                    "persons_in": enrich["entered"],
                    "persons_out": enrich["exited"],
                    # Full cleaned text for Details expand (no mid-sentence hard cut)
                    "message_full": msg_full,
                    "message_short": enrich.get("message_preview") or msg_full,
                    "has_person_change": bool(enrich["entered"] or enrich["exited"]),
                }
            )
        summary["capital"] = (
            f"{detail.get('capitalNominal')} {detail.get('capitalCurrency')}"
            if detail.get("capitalNominal")
            else None
        )
        zefix_detail = detail.get("zefixDetailWeb")
        if isinstance(zefix_detail, dict):
            summary["zefix_url"] = (
                zefix_detail.get("de") or zefix_detail.get("fr") or zefix_detail.get("en")
            )
        else:
            summary["zefix_url"] = None
        seed_summaries.append(summary)

        ehraid = detail.get("ehraid")
        if ehraid:
            seed_ehraids.add(int(ehraid))
        uid_d = _uid_digits(summary.get("uid"))
        if uid_d:
            seed_uids.add(uid_d)

        cid = _company_id(ehraid)
        graph.add_node({
            "id": cid,
            "type": "company",
            "label": summary.get("name") or "Unbekannt",
            "ehraid": ehraid,
            "uid": summary.get("uid"),
            "status": summary.get("status"),
            "canton": summary.get("canton"),
            "is_seed": True,
            "is_center": True,
            "min_level": 1,
        })

        timeline = build_person_timeline(detail.get("sogcPub"))
        # Zefix occasionally has firms with sogcDate but empty sogcPub (no SHAB text).
        if not timeline:
            care_name = care_of_display_name(detail.get("address"))
            if care_name:
                warnings.append(
                    f"Adress-c/o «{care_name}» — kein bestätigtes Organ, nur Zustellhinweis "
                    "(keine SHAB-Personenmeldungen bei Zefix)."
                )
                timeline = [
                    {
                        "id": re.sub(r"[^a-z0-9]+", "-", care_name.lower()).strip("-") or "care-of",
                        "name": care_name,
                        "roles": ["c/o Adresse"],
                        "residence": None,
                        "nationality": None,
                        "heimatort": None,
                        "status": "current",
                        "first_seen": None,
                        "last_seen": None,
                        "exited_date": None,
                        "source": "address_care_of",
                    }
                ]
        for person in timeline:
            pid = person["id"]
            nid = _person_id(pid)
            is_current = person.get("status") == "current"
            p_level = 1 if is_current else 2
            roles = person.get("roles") or []
            gender = infer_person_gender(roles)
            is_care_of = person.get("source") == "address_care_of"
            graph.add_node({
                "id": nid,
                "type": "person",
                "label": person.get("name"),
                "roles": roles,
                "gender": gender,
                "residence": person.get("residence"),
                "nationality": person.get("nationality"),
                "heimatort": person.get("heimatort"),
                "person_status": person.get("status"),
                "first_seen": person.get("first_seen"),
                "last_seen": person.get("last_seen"),
                "exited_date": person.get("exited_date"),
                "source": person.get("source") or "shab",
                "min_level": p_level,
            })
            role_label = ", ".join(roles) or (
                "Aktuell" if is_current else "Ehemalig"
            )
            p_status = person.get("status") if person.get("status") in (
                "current", "former"
            ) else None
            graph.add_edge(
                frm=nid,
                to=cid,
                label=role_label,
                edge_type="person_role",
                min_level=p_level,
                person_status=p_status,
            )
            graph.record_person_mandate(
                nid,
                company_name=summary.get("name"),
                company_uid=summary.get("uid"),
                ehraid=int(ehraid) if ehraid is not None else None,
                status=p_status,
            )
            meta = {
                "id": pid,
                "name": person.get("name"),
                "roles": roles,
                "gender": gender,
                "residence": person.get("residence"),
                "nationality": person.get("nationality"),
                "heimatort": person.get("heimatort"),
                "status": person.get("status"),
                "seed_company": summary.get("name"),
                "seed_uid": summary.get("uid"),
                "source": person.get("source") or "shab",
            }
            if is_current:
                current_persons[pid] = meta
            else:
                former_persons[pid] = meta
            # care-of is shown in graph/table but never used for L3+ SHAB expansion
            if not is_care_of and pid not in person_registry:
                person_registry[pid] = detail.get("registryOfCommerceId")

        # Level 2: structural Zefix links
        for field, label in _RELATION_FIELDS:
            for related in detail.get(field) or []:
                if not isinstance(related, dict) or not related.get("ehraid"):
                    continue
                rid = related.get("ehraid")
                rcid = _company_id(rid)
                graph.add_node({
                    "id": rcid,
                    "type": "company",
                    "label": related.get("name") or "Unbekannt",
                    "ehraid": rid,
                    "uid": _format_uid(str(related.get("uid") or "")) if related.get("uid") else None,
                    "status": related.get("status"),
                    "role_hint": label,
                    "is_seed": int(rid) in seed_ehraids if rid else False,
                    "min_level": 2,
                })
                graph.add_edge(
                    frm=cid,
                    to=rcid,
                    label=label,
                    edge_type="company_link",
                    min_level=2,
                )

    # ── Levels 3–4: person→company mandate expansion ───────────────────
    # Primary: Zefix SOGC / SHAB person month scan (nationwide).
    # Secondary fill-in: Moneyhouse relatedCompanies → Zefix firm resolve
    # (never sole authority; seed gate only disambiguates known Zefix organs).
    related_company_ehraids: set[int] = set()
    person_search_stats = {
        "searched": 0,
        "matches": 0,
        "skipped": 0,
        "years_back": 12,
        "elapsed_seconds": 0,
        "search_complete": True,
        "note": None,
        "moneyhouse_enabled": moneyhouse_person_search_enabled(),
        "moneyhouse_persons": 0,
        "moneyhouse_matches": 0,
        "moneyhouse_seed_confirmed": 0,
        "moneyhouse_identity_soft": 0,
        "moneyhouse_identity_rejected": 0,
        "shab_matches": 0,
        "method": "zefix+shab+moneyhouse",
        "identity_warnings": [],
        "identity_choices": [],
    }

    def _oldest_sogc_date(detail: dict | None) -> str | None:
        pubs = (detail or {}).get("sogcPub") or []
        dates = [p.get("sogcDate") for p in pubs if isinstance(p, dict) and p.get("sogcDate")]
        return min(dates) if dates else None

    def _years_between(earlier: str | None, later: str | None) -> float | None:
        if not earlier or not later:
            return None
        try:
            d0 = dt.date.fromisoformat(earlier[:10])
            d1 = dt.date.fromisoformat(later[:10])
            return round((d1 - d0).days / 365.25, 1)
        except ValueError:
            return None

    def _status_key(status: Any) -> Any:
        if isinstance(status, dict):
            return status.get("id") or status.get("key") or status.get("shortDescription") or status
        return status

    def _person_status_at_company(
        person_name: str | None,
        detail: dict | None,
    ) -> str | None:
        """Current|former for this person at a company from SHAB replay (if known)."""
        if not person_name or not isinstance(detail, dict):
            return None
        for p in build_person_timeline(detail.get("sogcPub")):
            if names_same_person(person_name, p.get("name")):
                st = p.get("status")
                if st in ("current", "former"):
                    return st
        return None

    async def _link_person_to_company(
        *,
        pid: str,
        min_level: int,
        ehraid: int,
        name: str | None,
        uid: str | None,
        status: Any,
        legal_seat: str | None,
        role_hint: str | None,
        person_linked_at: str | None,
        source_tag: str,
        detail: dict | None = None,
        person_status: str | None = None,
        person_name: str | None = None,
    ) -> bool:
        """Attach a company to a person with per-edge affiliation status.

        Seed firms are not double-counted as 'related', but the person→seed
        edge / mandate is still documented (bidirectional former links).
        Returns True when a new non-seed related firm was counted.
        """
        m_uid = _uid_digits(uid)
        is_seed = (m_uid and m_uid in seed_uids) or (ehraid in seed_ehraids)

        resolved = detail
        if resolved is None and not is_seed:
            try:
                resolved = await asyncio.to_thread(_zefix_get, f"/company/ehraid/{ehraid}")
            except Exception as e:
                logger.debug("Age enrich failed for %s: %s", ehraid, e)
                resolved = None
        if isinstance(resolved, dict):
            name = name or resolved.get("name")
            if not uid:
                uid = format_company_uid(resolved) or resolved.get("uid")
            if status is None:
                status = resolved.get("status")
            if not legal_seat:
                legal_seat = resolved.get("legalSeat")

        # Affiliation at this firm: prefer caller → SHAB timeline → default
        if person_status not in ("current", "former"):
            person_status = _person_status_at_company(
                person_name, resolved if isinstance(resolved, dict) else detail
            )
        if person_status not in ("current", "former") and is_seed:
            # Seed expand path already knows seed status; use meta if re-hit via MH
            meta = current_persons.get(pid) or former_persons.get(pid) or {}
            if meta.get("status") in ("current", "former"):
                person_status = meta.get("status")
        if person_status not in ("current", "former"):
            # MH active list / mention without timeline: unknown active → current
            person_status = "current"

        p_nid = _person_id(pid)
        c_nid = _company_id(ehraid)

        if is_seed:
            # Seed node already exists; never re-attach as related. Document/update
            # affiliation status on existing person↔seed edges + mandates.
            graph.record_person_mandate(
                p_nid,
                company_name=name,
                company_uid=uid,
                ehraid=ehraid,
                status=person_status,
            )
            p_res = graph.resolve_id(p_nid)
            for e in graph.edges:
                ends = {e.get("from"), e.get("to")}
                if ends != {p_res, c_nid}:
                    continue
                if e.get("type") not in ("person_role", "person_company"):
                    continue
                if person_status == "former" or not e.get("person_status"):
                    e["person_status"] = person_status
            return False

        already = ehraid in related_company_ehraids
        related_company_ehraids.add(ehraid)

        company_first_seen = None
        company_age_years = None
        likely_shell = False
        if isinstance(resolved, dict):
            company_first_seen = _oldest_sogc_date(resolved)
            company_age_years = _years_between(company_first_seen, person_linked_at)
            if company_age_years is not None and company_age_years >= 5:
                likely_shell = True

        graph.add_node({
            "id": c_nid,
            "type": "company",
            "label": name or "Unbekannt",
            "ehraid": ehraid,
            "uid": uid,
            "status": _status_key(status),
            "legal_seat": legal_seat,
            "role_hint": role_hint or source_tag,
            "person_linked_at": person_linked_at,
            "company_first_seen": company_first_seen,
            "company_age_years_at_link": company_age_years,
            "likely_shell_takeover": likely_shell,
            "mandate_source": source_tag,
            "is_seed": False,
            "min_level": min_level,
        })
        edge_label = role_hint or ("Mandat" if source_tag.startswith("moneyhouse") else "erwähnt")
        # Status lives on edge.person_status (dashed in UI); keep label as role text only.
        if likely_shell and person_status != "former":
            edge_label = f"{edge_label} · alte Firma"
        graph.add_edge(
            frm=p_nid,
            to=c_nid,
            label=edge_label,
            edge_type="person_company",
            min_level=min_level,
            person_status=person_status,
        )
        graph.record_person_mandate(
            p_nid,
            company_name=name,
            company_uid=uid,
            ehraid=ehraid,
            status=person_status,
        )
        return not already

    async def _expand_persons_shab(
        persons: dict[str, dict[str, Any]],
        *,
        min_level: int,
        items: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Zefix SOGC / SHAB month scan — primary mandate discovery path."""
        nonlocal person_search_stats
        if not items:
            return

        by_registry: dict[int | None, list[tuple[str, dict]]] = {}
        for pid, meta in items:
            by_registry.setdefault(person_registry.get(pid), []).append((pid, meta))

        # Full nationwide SHAB scan always first (formers often in other cantons).
        use_all_cantons = True
        years_back = 12
        max_seconds = 100.0

        for registry_id, group in by_registry.items():
            names = [meta["name"] for _, meta in group if meta.get("name")]
            exclude_uids = {
                meta["name"]: meta.get("seed_uid") or ""
                for _, meta in group
                if meta.get("name")
            }
            person_search_stats["searched"] += len(names)
            try:
                batch = await search_persons_batch(
                    names,
                    exclude_uids=exclude_uids,
                    registry_office_id=None if use_all_cantons else registry_id,
                    all_cantons=use_all_cantons,
                    years_back=years_back,
                    max_seconds=max_seconds,
                    deep=False,
                )
            except Exception as e:
                logger.warning(
                    "Batch person search failed (registry=%s): %s", registry_id, e
                )
                continue

            person_search_stats["elapsed_seconds"] = round(
                person_search_stats["elapsed_seconds"]
                + (batch.get("elapsed_seconds") or 0),
                2,
            )
            if batch.get("search_complete") is False:
                person_search_stats["search_complete"] = False
            if batch.get("note"):
                person_search_stats["note"] = batch.get("note")
            person_search_stats["years_back"] = batch.get("years_back") or 12

            name_to_pid = {
                meta["name"]: pid for pid, meta in group if meta.get("name")
            }
            for person_name, pid in name_to_pid.items():
                block = (batch.get("by_person") or {}).get(person_name) or {}
                for match in block.get("matches") or []:
                    ehraid = match.get("ehraid")
                    if not ehraid:
                        continue
                    ok = await _link_person_to_company(
                        pid=pid,
                        min_level=min_level,
                        ehraid=int(ehraid),
                        name=match.get("name"),
                        uid=match.get("uid"),
                        status=match.get("status"),
                        legal_seat=match.get("legal_seat"),
                        role_hint=match.get("role_hint") or "SHAB-Treffer",
                        person_linked_at=match.get("sogc_date"),
                        source_tag="shab",
                        detail=None,
                        person_name=person_name,
                    )
                    if ok:
                        person_search_stats["shab_matches"] += 1
                        person_search_stats["matches"] += 1

        person_search_stats["note"] = (
            person_search_stats.get("note")
            or "Mandate primär via Zefix/SHAB-Personensuche."
        )

    def _record_identity_choice(
        *,
        pid: str,
        meta: dict[str, Any],
        status: str,
        message: str,
        candidates: list[dict[str, Any]] | None,
        soft_person_key: str | None = None,
        technical: str | None = None,
    ) -> None:
        entry = {
            "person_name": meta.get("name") or "",
            "person_id": pid,
            "person_graph_id": _person_id(pid),
            "status": status,
            "message": message,
            "seed_company": meta.get("seed_company"),
            "seed_uid": meta.get("seed_uid"),
            "candidates": candidates or [],
            "can_accept_soft": status == "soft" and bool(soft_person_key),
            "soft_person_key": soft_person_key,
            "technical": technical,
        }
        person_search_stats["identity_choices"].append(entry)
        if message:
            line = f"{meta.get('name')}: {message}" if meta.get("name") else message
            if line not in person_search_stats["identity_warnings"]:
                person_search_stats["identity_warnings"].append(line)

    async def _expand_persons_moneyhouse(
        persons: dict[str, dict[str, Any]],
        *,
        min_level: int,
        items: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Moneyhouse fill-in: relatedCompanies → Zefix resolve (never primary)."""
        nonlocal person_search_stats
        if not moneyhouse_person_search_enabled():
            person_search_stats["note"] = (
                (person_search_stats.get("note") or "")
                + " Moneyhouse deaktiviert — nur Zefix/SHAB."
            ).strip()
            return

        shab_hits_before = int(person_search_stats.get("shab_matches") or 0)
        sem = asyncio.Semaphore(3)

        async def _link_mh_companies(
            *,
            pid: str,
            meta: dict[str, Any],
            companies: list[dict[str, Any]],
            source_tag: str,
        ) -> int:
            added = 0
            for company in companies or []:
                cname = (company.get("name") or "").strip()
                if not cname:
                    continue
                try:
                    detail = await resolve_company_detail(cname, None)
                except Exception as e:
                    logger.info(
                        "Zefix resolve failed for Moneyhouse firm %r: %s", cname, e
                    )
                    continue
                ehraid = detail.get("ehraid")
                if not ehraid:
                    continue
                ok = await _link_person_to_company(
                    pid=pid,
                    min_level=min_level,
                    ehraid=int(ehraid),
                    name=detail.get("name") or cname,
                    uid=format_company_uid(detail) or detail.get("uid"),
                    status=detail.get("status"),
                    legal_seat=detail.get("legalSeat"),
                    role_hint=None,
                    person_linked_at=company.get("from"),
                    source_tag=source_tag,
                    detail=detail,
                    person_name=meta.get("name"),
                )
                if ok:
                    added += 1
                    person_search_stats["moneyhouse_matches"] += 1
                    person_search_stats["matches"] += 1
            return added

        async def _one(pid: str, meta: dict[str, Any]) -> int:
            override = _override_for_person(
                identity_override_list,
                pid=pid,
                person_name=meta.get("name"),
            )
            if override and override.get("action") == "ignore":
                logger.info(
                    "Moneyhouse skipped (user ignore) for %r", meta.get("name")
                )
                return 0

            force_key = None
            if override and override.get("action") == "accept":
                force_key = override.get("moneyhouse_person_key") or None

            async with sem:
                try:
                    mh = await asyncio.to_thread(
                        search_person_mandates,
                        meta.get("name") or "",
                        residence=meta.get("residence"),
                        seed_company=meta.get("seed_company"),
                        seed_uid=meta.get("seed_uid"),
                        force_mh_person_key=force_key,
                    )
                except Exception as e:
                    logger.warning(
                        "Moneyhouse mandate search failed for %r: %s",
                        meta.get("name"),
                        e,
                    )
                    return 0

            person_search_stats["moneyhouse_persons"] += 1
            choices = list(mh.get("identity_choices") or [])
            note = mh.get("note") or ""
            tech = mh.get("note_technical")

            if not mh.get("matched_person"):
                # User already locked a profile key — do not re-surface the picker
                # when Moneyhouse fails to re-resolve that key on the re-scan.
                if force_key:
                    logger.warning(
                        "Moneyhouse force key %r not resolved for %r — skip identity UI",
                        force_key,
                        meta.get("name"),
                    )
                    return 0
                if note and (mh.get("viable_count") or 0) > 0:
                    person_search_stats["moneyhouse_identity_rejected"] += 1
                    _record_identity_choice(
                        pid=pid,
                        meta=meta,
                        status="ambiguous" if (mh.get("viable_count") or 0) > 1 else "none",
                        message=note,
                        candidates=choices,
                        technical=tech,
                    )
                    logger.info(
                        "Moneyhouse identity rejected for %r: %s",
                        meta.get("name"),
                        note,
                    )
                return 0

            # Soft-gate only: seed organ already known from Zefix/SHAB.
            # Seed confirmation prefers profiles that list the Zefix seed firm.
            identity = mh.get("identity_status")
            if not identity:
                identity = "confirmed" if mh.get("seed_confirmed") else "legacy"

            mp = mh.get("matched_person") or {}
            soft_key = mp.get("person_key") or mp.get("uri")

            if identity == "forced" or force_key:
                person_search_stats["moneyhouse_seed_confirmed"] += 1
                nid = graph.resolve_id(_person_id(pid))
                if nid in graph.nodes:
                    graph.nodes[nid]["moneyhouse_seed_confirmed"] = True
                    graph.nodes[nid]["moneyhouse_identity_status"] = "forced"
                source_tag = "moneyhouse+zefix+forced"
                return await _link_mh_companies(
                    pid=pid,
                    meta=meta,
                    companies=mh.get("companies") or [],
                    source_tag=source_tag,
                )

            if mh.get("seed_confirmed") or identity == "confirmed":
                person_search_stats["moneyhouse_seed_confirmed"] += 1
                nid = graph.resolve_id(_person_id(pid))
                if nid in graph.nodes:
                    graph.nodes[nid]["moneyhouse_seed_confirmed"] = True
                    graph.nodes[nid]["moneyhouse_identity_status"] = "confirmed"
                source_tag = "moneyhouse+zefix+seed"
                return await _link_mh_companies(
                    pid=pid,
                    meta=meta,
                    companies=mh.get("companies") or [],
                    source_tag=source_tag,
                )

            if identity == "soft":
                # Do not auto-import soft matches — user must confirm or ignore.
                person_search_stats["moneyhouse_identity_soft"] += 1
                warn = note or human_identity_note(
                    "soft",
                    person_name=meta.get("name"),
                    seed_name=meta.get("seed_company"),
                    seed_uid=meta.get("seed_uid"),
                )
                _record_identity_choice(
                    pid=pid,
                    meta=meta,
                    status="soft",
                    message=warn,
                    candidates=choices
                    or (
                        [
                            {
                                "person_key": soft_key,
                                "name": mp.get("name"),
                                "city": mp.get("residence"),
                                "name_score": mp.get("name_score") or mp.get("score"),
                                "seed_listed": False,
                                "related_companies": [
                                    c.get("name")
                                    for c in (mh.get("companies") or [])[:8]
                                    if c.get("name")
                                ],
                                "related_companies_count": len(mh.get("companies") or []),
                                "uri": mp.get("uri"),
                                "profile_url": mp.get("profile_url")
                                or mh_profile_url(
                                    str(mp.get("uri"))
                                    if mp.get("uri") is not None
                                    else None
                                ),
                            }
                        ]
                        if soft_key
                        else []
                    ),
                    soft_person_key=soft_key,
                    technical=tech,
                )
                logger.warning(
                    "Moneyhouse soft identity held for confirmation %r (seed=%r): %s",
                    meta.get("name"),
                    meta.get("seed_company"),
                    warn,
                )
                nid = graph.resolve_id(_person_id(pid))
                if nid in graph.nodes:
                    graph.nodes[nid]["moneyhouse_seed_confirmed"] = False
                    graph.nodes[nid]["moneyhouse_identity_status"] = "soft"
                    graph.nodes[nid]["identity_warning"] = warn
                return 0

            return 0

        await asyncio.gather(*[_one(pid, meta) for pid, meta in items])

        mh_added = int(person_search_stats.get("moneyhouse_matches") or 0)
        if mh_added:
            if shab_hits_before:
                person_search_stats["note"] = (
                    "Mandate via Zefix/SHAB; Moneyhouse-Nachzug für fehlende Firmen."
                )
            else:
                person_search_stats["note"] = (
                    "SHAB ohne Zusatzmandate; Moneyhouse→Zefix als Ergänzung."
                )
        elif moneyhouse_person_search_enabled() and not person_search_stats.get("note"):
            person_search_stats["note"] = (
                "Mandate via Zefix/SHAB (Moneyhouse ohne neue Firmen)."
            )

    async def _expand_persons(
        persons: dict[str, dict[str, Any]],
        *,
        min_level: int,
    ) -> None:
        """Zefix/SHAB first; Moneyhouse only after to enrich missing related firms."""
        nonlocal person_search_stats
        expandable = {
            pid: meta
            for pid, meta in persons.items()
            if (meta or {}).get("source") != "address_care_of"
        }
        items = list(expandable.items())[:max_person_searches]
        person_search_stats["skipped"] += max(0, len(expandable) - len(items))
        if not items:
            return

        await _expand_persons_shab(persons, min_level=min_level, items=items)
        await _expand_persons_moneyhouse(persons, min_level=min_level, items=items)

    if level >= 3 and current_persons:
        await _expand_persons(current_persons, min_level=3)
    if level >= 4 and former_persons:
        await _expand_persons(former_persons, min_level=4)

    # ── Level 5: persons at related companies ───────────────────────────
    if level >= 5 and related_company_ehraids:
        to_fetch = list(related_company_ehraids - seed_ehraids)[:12]

        async def _fetch_related(ehraid: int) -> None:
            try:
                detail = await asyncio.to_thread(_zefix_get, f"/company/ehraid/{ehraid}")
            except Exception as e:
                logger.warning("Related company fetch failed %s: %s", ehraid, e)
                return
            if not isinstance(detail, dict):
                return
            cid = _company_id(ehraid)
            company_label = detail.get("name")
            company_uid = format_company_uid(detail) or detail.get("uid")
            persons = collect_persons_from_publications(detail.get("sogcPub"))
            for person in persons[:8]:
                pid = person["id"]
                nid = _person_id(pid)
                roles = person.get("roles") or []
                p_status = person.get("status") or "current"
                if p_status not in ("current", "former"):
                    p_status = "current"
                # Status at the *related* firm only. Merge must not upgrade a
                # seed-former to "current" (handled in _GraphBuilder.add_node).
                graph.add_node({
                    "id": nid,
                    "type": "person",
                    "label": person.get("name"),
                    "roles": roles,
                    "gender": infer_person_gender(roles),
                    "residence": person.get("residence"),
                    "nationality": person.get("nationality"),
                    "heimatort": person.get("heimatort"),
                    "person_status": p_status,
                    "min_level": 5,
                })
                graph.add_edge(
                    frm=nid,
                    to=cid,
                    label=", ".join(person.get("roles") or []) or "Eingetragen",
                    edge_type="person_role",
                    min_level=5,
                    person_status=p_status,
                )
                graph.record_person_mandate(
                    nid,
                    company_name=company_label,
                    company_uid=company_uid,
                    ehraid=ehraid,
                    status=p_status,
                )

        await asyncio.gather(*[_fetch_related(e) for e in to_fetch])

    nodes, edges = graph.export(level)

    persons_table: list[dict[str, Any]] = [
        {
            **meta,
            "person_id": pid,
            "status": "current",
        }
        for pid, meta in current_persons.items()
    ] + [
        {
            **meta,
            "person_id": pid,
            "status": "former",
        }
        for pid, meta in former_persons.items()
    ]
    # Attach graph mandates (seed + related) so the persons sidebar lists firms after L3+
    for entry in persons_table:
        pid = entry.get("person_id") or entry.get("id")
        if not pid:
            continue
        nid = graph.resolve_id(_person_id(str(pid)))
        node = graph.nodes.get(nid)
        if not node:
            # Name fallback after middle-name collapse
            for n in graph.nodes.values():
                if n.get("type") != "person":
                    continue
                if names_same_person(entry.get("name"), n.get("label") or n.get("name")):
                    node = n
                    break
        if node and node.get("mandates"):
            entry["mandates"] = list(node["mandates"])

    result = {
        "level": level,
        "level_label": LEVEL_LABELS.get(level, ""),
        "level_labels": LEVEL_LABELS,
        "seed_companies": seed_summaries,
        "errors": errors,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "seed_count": len(seed_summaries),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "current_persons": len(current_persons),
            "former_persons": len(former_persons),
            "person_search": person_search_stats,
        },
        "persons_table": persons_table,
    }
    try:
        from app.hr_network.case_flags import annotate_network_with_case_flags

        result = await annotate_network_with_case_flags(result)
    except Exception as e:
        logger.debug("Case-flag annotation skipped: %s", e)
    return result
