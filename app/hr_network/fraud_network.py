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
from app.hr_network.person_search import search_persons_batch
from app.hr_network.shab_parser import (
    build_person_timeline,
    collect_persons_from_publications,
    infer_person_gender,
)
from app.hr_network.service import _format_address, _legal_form_label
from app.hr_network.zefix_resolve import resolve_company_detail, uid_digits

logger = logging.getLogger(__name__)

# Level meanings (min_level on nodes/edges):
# 1 — Seed firms + current owners
# 2 — + former owners + Zefix structural company links
# 3 — + other firms of current owners (SHAB person search)
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
    1: "Firma + aktuelle Inhaber",
    2: "Ehemalige Inhaber + Firmenstruktur",
    3: "Weitere Firmen aktueller Personen",
    4: "Weitere Firmen ehemaliger Personen",
    5: "Personen der verbundenen Firmen",
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
    return {
        "name": detail.get("name"),
        "ehraid": detail.get("ehraid"),
        "uid": _format_uid(uid_raw) if uid_raw else None,
        "status": detail.get("status"),
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


class _GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._edge_keys: set[tuple] = set()

    def add_node(self, node: dict[str, Any]) -> None:
        nid = node["id"]
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
            # Never promote former → current. Same person can be former at the
            # seed and current at a related firm (level 5); seed-centric status
            # must stay "former" so they are not shown as active seed officers.
            if existing.get("person_status") != "former" and node.get("person_status") == "current":
                existing["person_status"] = "current"
            elif not existing.get("person_status") and node.get("person_status"):
                existing["person_status"] = node["person_status"]
            for key in ("roles", "label", "uid", "residence", "nationality", "heimatort"):
                if node.get(key) and not existing.get(key):
                    existing[key] = node[key]
            return
        self.nodes[nid] = node

    def add_edge(
        self,
        *,
        frm: str,
        to: str,
        label: str,
        edge_type: str,
        min_level: int,
    ) -> None:
        key = (frm, to, label, edge_type)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append({
            "from": frm,
            "to": to,
            "label": label,
            "type": edge_type,
            "min_level": min_level,
        })

    def export(self, level: int) -> tuple[list[dict], list[dict]]:
        nodes = [n for n in self.nodes.values() if n.get("min_level", 1) <= level]
        node_ids = {n["id"] for n in nodes}
        edges = [
            e for e in self.edges
            if e.get("min_level", 1) <= level
            and e["from"] in node_ids
            and e["to"] in node_ids
        ]
        return nodes, edges


async def build_fraud_network(
    *,
    level: int = 2,
    company_ids: list[str] | None = None,
    ad_hoc_company: dict[str, str] | None = None,
    max_person_searches: int = 8,
) -> dict[str, Any]:
    """
    Build multi-level network from fraud list and/or a single ad-hoc company.

    Levels 1–2 use Zefix/SHAB only (fast).
    Levels 3–5 add cross-company person search (slower).

    If ``ad_hoc_company`` is set (``{"name": ..., "uid": ...}``), that firm is
    the (additional) seed — enabling single-company deep analysis without a
    prior list entry. When only ``ad_hoc_company`` is provided (no company_ids
    and empty fraud list is OK), analysis runs on that seed alone.
    """
    if not config.ZEFIX_USERNAME or not config.ZEFIX_PASSWORD:
        raise PermissionError("Zefix-Zugangsdaten fehlen")

    level = max(1, min(5, int(level)))

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
        summary["recent_publications"] = [
            {
                "date": pub.get("sogcDate"),
                "types": [
                    t.get("key", "")
                    for t in (pub.get("mutationTypes") or [])
                    if isinstance(t, dict)
                ],
                "types_de": [
                    _label_for_key(t.get("key", ""))
                    for t in (pub.get("mutationTypes") or [])
                    if isinstance(t, dict) and t.get("key")
                ],
                "message_short": _strip(pub.get("message", ""))[:280],
            }
            for pub in pubs[:60]
        ]
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
        for person in timeline:
            pid = person["id"]
            nid = _person_id(pid)
            is_current = person.get("status") == "current"
            p_level = 1 if is_current else 2
            roles = person.get("roles") or []
            gender = infer_person_gender(roles)
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
                "min_level": p_level,
            })
            role_label = ", ".join(roles) or (
                "Aktuell" if is_current else "Ehemalig"
            )
            graph.add_edge(
                frm=nid,
                to=cid,
                label=role_label,
                edge_type="person_role",
                min_level=p_level,
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
            }
            if is_current:
                current_persons[pid] = meta
            else:
                former_persons[pid] = meta
            if pid not in person_registry:
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

    # ── Levels 3–4: cross-company person search ─────────────────────────
    related_company_ehraids: set[int] = set()
    person_search_stats = {
        "searched": 0,
        "matches": 0,
        "skipped": 0,
        "years_back": 12,
        "elapsed_seconds": 0,
        "search_complete": True,
        "note": None,
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

    async def _expand_persons(
        persons: dict[str, dict[str, Any]],
        *,
        min_level: int,
    ) -> None:
        """One SHAB batch scan per registry — covers ~12y history for all owners."""
        nonlocal person_search_stats
        items = list(persons.items())[:max_person_searches]
        person_search_stats["skipped"] += max(0, len(persons) - len(items))
        if not items:
            return

        # Group by registry so each canton is scanned once for all persons there.
        by_registry: dict[int | None, list[tuple[str, dict]]] = {}
        for pid, meta in items:
            by_registry.setdefault(person_registry.get(pid), []).append((pid, meta))

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
                    registry_office_id=registry_id,
                    years_back=12,
                    max_seconds=80.0,
                    deep=False,
                )
            except Exception as e:
                logger.warning("Batch person search failed (registry=%s): %s", registry_id, e)
                continue

            person_search_stats["elapsed_seconds"] = round(
                person_search_stats["elapsed_seconds"] + (batch.get("elapsed_seconds") or 0),
                2,
            )
            if batch.get("search_complete") is False:
                person_search_stats["search_complete"] = False
            if batch.get("note"):
                person_search_stats["note"] = batch.get("note")
            person_search_stats["years_back"] = batch.get("years_back") or 12

            name_to_pid = {meta["name"]: pid for pid, meta in group if meta.get("name")}
            for person_name, pid in name_to_pid.items():
                block = (batch.get("by_person") or {}).get(person_name) or {}
                for match in block.get("matches") or []:
                    m_uid = _uid_digits(match.get("uid"))
                    if m_uid and m_uid in seed_uids:
                        continue
                    ehraid = match.get("ehraid")
                    if not ehraid:
                        continue
                    ehraid = int(ehraid)
                    if ehraid in seed_ehraids:
                        continue
                    related_company_ehraids.add(ehraid)
                    person_search_stats["matches"] += 1

                    # Enrich with company age → money-mule signal (old firm, newer person link).
                    company_first_seen = None
                    company_age_years = None
                    likely_shell = False
                    try:
                        detail = await asyncio.to_thread(_zefix_get, f"/company/ehraid/{ehraid}")
                        if isinstance(detail, dict):
                            company_first_seen = _oldest_sogc_date(detail)
                            company_age_years = _years_between(
                                company_first_seen, match.get("sogc_date")
                            )
                            # Firma schon länger existent, Person erst später eingetragen.
                            if company_age_years is not None and company_age_years >= 5:
                                likely_shell = True
                    except Exception as e:
                        logger.debug("Age enrich failed for %s: %s", ehraid, e)

                    rcid = _company_id(ehraid)
                    graph.add_node({
                        "id": rcid,
                        "type": "company",
                        "label": match.get("name") or "Unbekannt",
                        "ehraid": ehraid,
                        "uid": match.get("uid"),
                        "status": match.get("status"),
                        "legal_seat": match.get("legal_seat"),
                        "role_hint": match.get("role_hint") or "SHAB-Treffer",
                        "person_linked_at": match.get("sogc_date"),
                        "company_first_seen": company_first_seen,
                        "company_age_years_at_link": company_age_years,
                        "likely_shell_takeover": likely_shell,
                        "is_seed": False,
                        "min_level": min_level,
                    })
                    edge_label = match.get("role_hint") or "erwähnt"
                    if likely_shell:
                        edge_label = f"{edge_label} · alte Firma"
                    graph.add_edge(
                        frm=_person_id(pid),
                        to=rcid,
                        label=edge_label,
                        edge_type="person_company",
                        min_level=min_level,
                    )

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
            persons = collect_persons_from_publications(detail.get("sogcPub"))
            for person in persons[:8]:
                pid = person["id"]
                nid = _person_id(pid)
                roles = person.get("roles") or []
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
                    "person_status": person.get("status") or "current",
                    "min_level": 5,
                })
                graph.add_edge(
                    frm=nid,
                    to=cid,
                    label=", ".join(person.get("roles") or []) or "Eingetragen",
                    edge_type="person_role",
                    min_level=5,
                )

        await asyncio.gather(*[_fetch_related(e) for e in to_fetch])

    nodes, edges = graph.export(level)

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
        "persons_table": [
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
        ],
    }
    try:
        from app.hr_network.case_flags import annotate_network_with_case_flags

        result = await annotate_network_with_case_flags(result)
    except Exception as e:
        logger.debug("Case-flag annotation skipped: %s", e)
    return result
