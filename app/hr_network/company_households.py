"""Group watched companies into 'households' via shared persons."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from app.database import PersonCompanyLink, WatchedPerson, async_session
from app.hr_network.fraud_network_cache import load_cached_for_company
from app.hr_network.person_names import parse_person_name_parts
from app.hr_network.watched_companies import CACHE_LEVEL, _name_key, _uid_digits


class _UnionFind:
    def __init__(self, ids: list[int]) -> None:
        self.parent = {i: i for i in ids}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _company_key(name: str | None, uid: str | None) -> str:
    digits = _uid_digits(uid)
    if digits:
        return f"u:{digits}"
    nk = _name_key(name)
    return f"n:{nk}" if nk else ""


def _person_key(name: str | None) -> str:
    parts = parse_person_name_parts(name or "")
    last = (parts.get("last_name") or "").lower()
    first = " ".join(parts.get("first_parts") or []).lower()
    if last and first:
        return f"{last}|{first.split()[0]}"
    return (name or "").strip().lower()


def _union_ids(uf: _UnionFind, ids: list[int]) -> None:
    ids = [i for i in ids if i in uf.parent]
    if len(ids) < 2:
        return
    head = ids[0]
    for other in ids[1:]:
        uf.union(head, other)


def build_households(
    items: list[dict[str, Any]],
    *,
    person_links: list[dict[str, Any]] | None = None,
    graphs: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Connected components: two watched firms share a household if the same
    person is linked to both (watchlist links or cached network graph).
    """
    if not items:
        return []
    by_id = {int(it["id"]): it for it in items if it.get("id") is not None}
    uf = _UnionFind(list(by_id.keys()))
    key_to_ids: dict[str, list[int]] = defaultdict(list)
    for cid, it in by_id.items():
        k = _company_key(it.get("company_name"), it.get("company_uid"))
        if k:
            key_to_ids[k].append(cid)

    person_to_ids: dict[str, set[int]] = defaultdict(set)
    person_labels: dict[str, str] = {}

    for link in person_links or []:
        pk = _person_key(link.get("person_name"))
        if not pk:
            continue
        ck = _company_key(link.get("company_name"), link.get("company_uid"))
        for cid in key_to_ids.get(ck, []):
            person_to_ids[pk].add(cid)
        label = (link.get("person_name") or "").strip()
        if label and pk not in person_labels:
            person_labels[pk] = label

    for cid, graph in (graphs or {}).items():
        if cid not in by_id:
            continue
        nodes = {n.get("id"): n for n in (graph.get("nodes") or []) if isinstance(n, dict)}
        for edge in graph.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            a, b = nodes.get(edge.get("from")), nodes.get(edge.get("to"))
            if not a or not b:
                continue
            person, company = None, None
            if a.get("type") == "person" and b.get("type") == "company":
                person, company = a, b
            elif b.get("type") == "person" and a.get("type") == "company":
                person, company = b, a
            if not person or not company:
                continue
            pk = _person_key(person.get("label") or person.get("name"))
            if not pk:
                continue
            ck = _company_key(company.get("label") or company.get("name"), company.get("uid"))
            ids = set(key_to_ids.get(ck, []))
            ids.add(cid)
            person_to_ids[pk].update(ids)
            label = (person.get("label") or person.get("name") or "").strip()
            if label and pk not in person_labels:
                person_labels[pk] = label

    for ids in person_to_ids.values():
        _union_ids(uf, list(ids))

    buckets: dict[int, list[int]] = defaultdict(list)
    for cid in by_id:
        buckets[uf.find(cid)].append(cid)

    households: list[dict[str, Any]] = []
    for root, cids in buckets.items():
        members = [by_id[i] for i in sorted(cids, key=lambda i: (by_id[i].get("company_name") or "").lower())]
        connectors: list[tuple[int, str]] = []
        for pk, ids in person_to_ids.items():
            overlap = ids.intersection(cids)
            if len(overlap) >= 2:
                connectors.append((len(overlap), person_labels.get(pk) or pk))
        connectors.sort(key=lambda x: (-x[0], x[1].lower()))
        people = [name for _, name in connectors[:3]]
        if people:
            title = " · ".join(people[:2])
        elif len(members) > 1:
            title = "Verbundene Firmen"
        else:
            title = members[0].get("company_name") or "Firma"
        hid = f"h{root}"
        for m in members:
            m["household_id"] = hid
        households.append(
            {
                "id": hid,
                "title": title,
                "people": people,
                "size": len(members),
                "company_ids": [m["id"] for m in members],
                "items": members,
            }
        )

    households.sort(key=lambda h: (-h["size"], (h["title"] or "").lower()))
    return households


async def _person_links_from_db() -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(PersonCompanyLink, WatchedPerson).join(
                        WatchedPerson, PersonCompanyLink.person_id == WatchedPerson.id
                    )
                )
            ).all()
        )
        out: list[dict[str, Any]] = []
        for link, person in rows:
            out.append(
                {
                    "person_name": person.display_name,
                    "company_name": link.company_name,
                    "company_uid": link.company_uid,
                }
            )
            if person.source_company_name or person.source_company_ehraid:
                out.append(
                    {
                        "person_name": person.display_name,
                        "company_name": person.source_company_name or "",
                        "company_uid": None,
                    }
                )
        return out


def _graphs_from_cache(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    graphs: dict[int, dict[str, Any]] = {}
    for it in items:
        hit, _key = load_cached_for_company(
            level=CACHE_LEVEL,
            company_name=it.get("company_name"),
            company_uid=it.get("company_uid"),
        )
        if hit:
            graphs[int(it["id"])] = hit
    return graphs


async def attach_households(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links = await _person_links_from_db()
    graphs = _graphs_from_cache(items)
    return build_households(items, person_links=links, graphs=graphs)
