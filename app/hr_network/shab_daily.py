"""Daily CH-wide SHAB publication ingest (go-forward archive) + optional watchlist match.

Complementary to Moneyhouse person monitoring: one ZefixREST day-window fetch
stores all publications locally; Phase-2 matching flips the index
(publication persons → watched_persons).

Multi-month backfill is intentionally deferred — see ``backfill_stub`` / docs.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

import config
from app.checks.zefix_check import _format_uid
from app.database import (
    NetworkAlert,
    PersonCompanyLink,
    ShabDailyIngestRun,
    ShabDailyMatch,
    ShabDailyPublication,
    WatchedPerson,
    async_session,
)
from app.hr_network.person_names import names_same_person
from app.hr_network.shab_parser import iter_named_persons_in_message
from app.hr_network.zefix_rest import zefix_rest_post

logger = logging.getLogger(__name__)

_MONITORABLE = ("active", "confirmed_fraud")
_SOURCE = "shab_daily"
_MAX_ENTRIES = 5000


def ingest_enabled() -> bool:
    return bool(getattr(config, "SHAB_DAILY_INGEST", False))


def match_enabled() -> bool:
    return bool(getattr(config, "SHAB_DAILY_MATCH", True))


def default_window(
    *,
    today: dt.date | None = None,
) -> tuple[dt.date, dt.date]:
    """Yesterday → today (covers late Zefix indexing of yesterday's SOGC day)."""
    end = today or dt.date.today()
    return end - dt.timedelta(days=1), end


def _mutation_keys(pub: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for t in pub.get("mutationTypes") or []:
        if isinstance(t, dict) and t.get("key"):
            keys.append(str(t["key"]))
    return keys


def _parse_person_summaries(message: str, *, sogc_date: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for person in iter_named_persons_in_message(message, sogc_date=sogc_date):
        out.append(
            {
                "name": person.get("name"),
                "roles": list(person.get("roles") or []),
                "section": person.get("section"),
                "residence": person.get("residence"),
            }
        )
    return out


def _pub_natural_key(pub: dict[str, Any], item: dict[str, Any]) -> str | None:
    shab_id = pub.get("shabId")
    if shab_id is not None and str(shab_id).strip() != "":
        return str(int(shab_id)) if str(shab_id).isdigit() else str(shab_id)
    # Rare fallback when shabId missing
    ehraid = item.get("ehraid")
    journal = pub.get("registryOfficeJournalId")
    date = pub.get("shabDate") or ""
    if ehraid and journal and date:
        return f"fallback:{ehraid}:{journal}:{date}"
    return None


def flatten_shab_search_page(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Company list + nested shabPub[] → one dict per publication."""
    rows: list[dict[str, Any]] = []
    for item in data.get("list") or []:
        if not isinstance(item, dict):
            continue
        uid_raw = str(item.get("uid") or "")
        company_uid = item.get("uidFormatted") or (_format_uid(uid_raw) if uid_raw else None)
        ehraid = item.get("ehraid")
        try:
            ehraid_i = int(ehraid) if ehraid is not None else None
        except (TypeError, ValueError):
            ehraid_i = None
        for pub in item.get("shabPub") or []:
            if not isinstance(pub, dict):
                continue
            key = _pub_natural_key(pub, item)
            if not key:
                continue
            message = pub.get("message") or ""
            sogc = pub.get("shabDate") or item.get("shabDate")
            canton = pub.get("registryOfficeCanton")
            if not canton and isinstance(item.get("address"), dict):
                # address rarely carries canton; prefer registryOfficeCanton
                canton = item.get("address", {}).get("region") or None
            rows.append(
                {
                    "shab_id": key,
                    "publication_date": (sogc or "")[:10] or None,
                    "company_name": item.get("name"),
                    "company_uid": company_uid,
                    "company_ehraid": ehraid_i,
                    "canton": (str(canton).strip().upper()[:8] if canton else None),
                    "registry_office_id": pub.get("registryOfficeId")
                    or item.get("registerOfficeId"),
                    "mutation_types": _mutation_keys(pub),
                    "message": message,
                    "person_names": _parse_person_summaries(
                        message, sogc_date=str(sogc)[:10] if sogc else None
                    ),
                    "journal_id": pub.get("registryOfficeJournalId"),
                    "journal_date": (pub.get("registryOfficeJournalDate") or "")[:10] or None,
                }
            )
    return rows


def fetch_shab_day(
    day_start: dt.date,
    day_end: dt.date | None = None,
    *,
    registry_offices: list[int] | None = None,
) -> dict[str, Any]:
    """
    CH-wide (default) ZefixREST SHAB search for a publication-date window.

    No ``registryOffices`` ⇒ whole Switzerland (e.g. GE takeover visible even
    when watchlist seeds are ZH-only).
    """
    end = day_end or day_start
    if end < day_start:
        day_start, end = end, day_start

    offset = 0
    pages = 0
    pubs: list[dict[str, Any]] = []
    seen: set[str] = set()

    while True:
        payload: dict[str, Any] = {
            "maxEntries": _MAX_ENTRIES,
            "offset": offset,
            "publicationDate": day_start.isoformat(),
            "publicationDateEnd": end.isoformat(),
        }
        if registry_offices:
            payload["registryOffices"] = registry_offices

        data = zefix_rest_post("/shab/search.json", payload)
        pages += 1
        for row in flatten_shab_search_page(data if isinstance(data, dict) else {}):
            sid = row["shab_id"]
            if sid in seen:
                continue
            seen.add(sid)
            pubs.append(row)

        if not isinstance(data, dict) or not data.get("hasMoreResults"):
            break
        offset = int(data.get("maxOffset") or (offset + _MAX_ENTRIES))
        if pages > 50:
            logger.warning(
                "SHAB daily: pagination safety stop after %s pages (offset=%s)",
                pages,
                offset,
            )
            break

    return {
        "window_start": day_start.isoformat(),
        "window_end": end.isoformat(),
        "pages": pages,
        "publications": pubs,
        "count": len(pubs),
        "ch_wide": not bool(registry_offices),
    }


async def upsert_publications(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Idempotent upsert by ``shab_id``. Re-running the same day is safe."""
    if not rows:
        return {"upserted": 0, "inserted": 0, "updated": 0}

    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0

    async with async_session() as session:
        for row in rows:
            sid = str(row["shab_id"])
            values = {
                "shab_id": sid,
                "publication_date": row.get("publication_date"),
                "company_name": (row.get("company_name") or "")[:512] or None,
                "company_uid": (row.get("company_uid") or None),
                "company_ehraid": row.get("company_ehraid"),
                "canton": (row.get("canton") or None),
                "registry_office_id": row.get("registry_office_id"),
                "mutation_types": row.get("mutation_types") or [],
                "message": row.get("message") or "",
                "person_names": row.get("person_names") or [],
                "journal_id": row.get("journal_id"),
                "journal_date": row.get("journal_date"),
                "updated_at": now,
            }
            existing = (
                await session.execute(
                    select(ShabDailyPublication).where(ShabDailyPublication.shab_id == sid)
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    ShabDailyPublication(
                        **values,
                        ingested_at=now,
                    )
                )
                inserted += 1
            else:
                for key, val in values.items():
                    if key == "shab_id":
                        continue
                    setattr(existing, key, val)
                updated += 1
        await session.commit()

    return {"upserted": inserted + updated, "inserted": inserted, "updated": updated}


def _match_confidence(watched: WatchedPerson, person: dict[str, Any]) -> str:
    score = 1.5  # names_same_person already passed
    wr = (watched.residence or "").strip().lower()
    pr = (person.get("residence") or "").strip().lower()
    if wr and pr and (wr in pr or pr in wr):
        score += 1
    if person.get("roles"):
        score += 0.5
    if score >= 2.5:
        return "high"
    if score >= 1.5:
        return "medium"
    return "low"


def _alert_type_for_roles(roles: list[str] | None, *, section: str | None) -> tuple[str, str]:
    if (section or "").lower() == "exited":
        return "organ_exit", "medium"
    role = ", ".join(roles or []).lower()
    if any(h in role for h in ("inhaber", "gründer", "grunder", "fondateur", "fondatrice")):
        return "new_company_founded", "high"
    if not roles:
        return "new_company_founded", "medium"
    return "new_role", "medium"


async def match_publications_against_watchlist(
    *,
    publication_dates: list[str] | None = None,
    shab_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Match stored pubs' parsed persons to monitorable watchlist entries."""
    async with async_session() as session:
        people = list(
            (
                await session.execute(
                    select(WatchedPerson).where(WatchedPerson.status.in_(_MONITORABLE))
                )
            )
            .scalars()
            .all()
        )
        if not people:
            return {
                "matched": 0,
                "alerts": 0,
                "new_links": 0,
                "skipped_existing": 0,
                "note": "keine monitorbaren Watchlist-Personen",
            }

        q = select(ShabDailyPublication)
        if shab_ids:
            q = q.where(ShabDailyPublication.shab_id.in_([str(s) for s in shab_ids]))
        elif publication_dates:
            q = q.where(ShabDailyPublication.publication_date.in_(publication_dates))
        pubs = list((await session.execute(q)).scalars().all())
        pub_ids = [p.shab_id for p in pubs]
        if pub_ids:
            existing_matches = {
                (m.shab_id, m.person_id)
                for m in (
                    await session.execute(
                        select(ShabDailyMatch).where(ShabDailyMatch.shab_id.in_(pub_ids))
                    )
                )
                .scalars()
                .all()
            }
        else:
            existing_matches = set()

        # Preload company links per watched person (avoid N+1)
        person_ids = [p.id for p in people]
        links_by_person: dict[int, list[PersonCompanyLink]] = {pid: [] for pid in person_ids}
        if person_ids:
            for lnk in (
                await session.execute(
                    select(PersonCompanyLink).where(PersonCompanyLink.person_id.in_(person_ids))
                )
            ).scalars().all():
                links_by_person.setdefault(lnk.person_id, []).append(lnk)

        alerts_created = 0
        links_created = 0
        skipped = 0
        digest: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for pub in pubs:
            persons = pub.person_names or []
            if not isinstance(persons, list):
                continue
            for person in persons:
                if not isinstance(person, dict):
                    continue
                label = (person.get("name") or "").strip()
                if not label:
                    continue
                for watched in people:
                    if not names_same_person(watched.display_name, label):
                        continue
                    key = (pub.shab_id, watched.id)
                    if key in existing_matches:
                        skipped += 1
                        continue

                    confidence = _match_confidence(watched, person)
                    roles = list(person.get("roles") or [])
                    role_hint = ", ".join(roles) or None
                    alert_type, severity = _alert_type_for_roles(
                        roles, section=person.get("section")
                    )
                    if confidence == "low":
                        severity = "low"

                    # Link if company not already known for this person
                    links = links_by_person.get(watched.id) or []
                    known_eh = {lnk.company_ehraid for lnk in links if lnk.company_ehraid}
                    known_names = {(lnk.company_name or "").lower() for lnk in links}
                    cname = pub.company_name or "Unbekannt"
                    ehraid = pub.company_ehraid
                    already_linked = (ehraid and ehraid in known_eh) or (
                        cname.lower() in known_names
                    )
                    if not already_linked:
                        new_link = PersonCompanyLink(
                            person_id=watched.id,
                            company_ehraid=ehraid,
                            company_name=cname,
                            company_uid=pub.company_uid,
                            role=role_hint,
                            relation_type="newly_found",
                            is_seed_company=False,
                            first_detected_at=now,
                            match_confidence=confidence,
                        )
                        session.add(new_link)
                        links_by_person.setdefault(watched.id, []).append(new_link)
                        links_created += 1

                    section = person.get("section") or "entered"
                    msg = (
                        f"«{watched.display_name}» in SHAB «{cname}»"
                        + (f" ({role_hint})" if role_hint else "")
                        + f" [{section}] — Konfidenz: {confidence}"
                        + f" · Quelle: {_SOURCE} · SHAB-ID {pub.shab_id}"
                        + (f" · {pub.publication_date}" if pub.publication_date else "")
                        + (f" · {pub.canton}" if pub.canton else "")
                    )[:1024]
                    alert = NetworkAlert(
                        alert_type=alert_type,
                        person_id=watched.id,
                        company_ehraid=ehraid,
                        company_name=cname,
                        severity=severity,
                        message=msg,
                        created_at=now,
                        acknowledged=False,
                    )
                    session.add(alert)
                    await session.flush()
                    session.add(
                        ShabDailyMatch(
                            shab_id=pub.shab_id,
                            person_id=watched.id,
                            company_ehraid=ehraid,
                            company_name=cname,
                            matched_name=label,
                            alert_id=alert.id,
                            created_at=now,
                        )
                    )
                    existing_matches.add(key)
                    alerts_created += 1
                    digest.append(
                        {
                            "alert_type": alert_type,
                            "severity": severity,
                            "company_name": cname,
                            "confidence": confidence,
                            "source": _SOURCE,
                            "message": msg,
                            "person_name": watched.display_name,
                            "person_id": watched.id,
                        }
                    )

        await session.commit()

    email_result: dict[str, Any] | None = None
    if digest:
        from app.notify_email import notify_watchlist_new_hits

        email_result = notify_watchlist_new_hits(digest, source="shab_daily_batch")
    else:
        email_result = {"sent": False, "reason": "no_alerts"}

    return {
        "matched": alerts_created,
        "alerts": alerts_created,
        "new_links": links_created,
        "skipped_existing": skipped,
        "email": email_result,
    }


async def run_shab_daily_ingest(
    *,
    day_start: dt.date | None = None,
    day_end: dt.date | None = None,
    match: bool | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch + upsert day window; optionally match watchlist. Idempotent."""
    if not force and not ingest_enabled():
        return {
            "skipped": True,
            "reason": "SHAB_DAILY_INGEST disabled",
            "hint": "Setze SHAB_DAILY_INGEST=1",
        }

    start, end = default_window()
    if day_start is not None:
        start = day_start
    if day_end is not None:
        end = day_end
    elif day_start is not None:
        end = day_start

    do_match = match_enabled() if match is None else bool(match)
    started = datetime.now(timezone.utc)
    run = ShabDailyIngestRun(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        started_at=started,
        status="running",
        pubs_fetched=0,
        pubs_upserted=0,
        pubs_inserted=0,
        alerts_created=0,
        pages_fetched=0,
        ch_wide=True,
    )
    async with async_session() as session:
        session.add(run)
        await session.commit()
        run_id = run.id

    try:
        fetched = await asyncio.to_thread(fetch_shab_day, start, end)
        pubs = fetched.get("publications") or []
        upsert_stats = await upsert_publications(pubs)
        match_stats: dict[str, Any] = {"skipped": True}
        if do_match and pubs:
            dates = sorted(
                {
                    str(p.get("publication_date"))
                    for p in pubs
                    if p.get("publication_date")
                }
            )
            # Also include window days even if empty list edge
            for d in (start, end):
                iso = d.isoformat()
                if iso not in dates:
                    dates.append(iso)
            match_stats = await match_publications_against_watchlist(
                publication_dates=dates,
            )
        elif do_match:
            match_stats = await match_publications_against_watchlist(
                publication_dates=[start.isoformat(), end.isoformat()],
            )

        finished = datetime.now(timezone.utc)
        async with async_session() as session:
            row = await session.get(ShabDailyIngestRun, run_id)
            if row:
                row.finished_at = finished
                row.status = "ok"
                row.pubs_fetched = int(fetched.get("count") or 0)
                row.pubs_upserted = int(upsert_stats.get("upserted") or 0)
                row.pubs_inserted = int(upsert_stats.get("inserted") or 0)
                row.alerts_created = int(match_stats.get("alerts") or 0)
                row.pages_fetched = int(fetched.get("pages") or 0)
                row.error_message = None
                await session.commit()

        result = {
            "skipped": False,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "pages": fetched.get("pages"),
            "fetched": fetched.get("count"),
            "upsert": upsert_stats,
            "match": match_stats,
            "run_id": run_id,
            "ch_wide": True,
            "retention_note": (
                "Rohdaten unbegrenzt (Phase-3 Retention-Job später; "
                "Empfehlung ≥90 Tage behalten)."
            ),
        }
        logger.info(
            "SHAB daily ingest ok: window=%s..%s fetched=%s upserted=%s alerts=%s",
            start,
            end,
            result["fetched"],
            upsert_stats.get("upserted"),
            match_stats.get("alerts"),
        )
        return result
    except Exception as e:
        logger.exception("SHAB daily ingest failed")
        async with async_session() as session:
            row = await session.get(ShabDailyIngestRun, run_id)
            if row:
                row.finished_at = datetime.now(timezone.utc)
                row.status = "error"
                row.error_message = str(e)[:1000]
                await session.commit()
        return {
            "skipped": False,
            "error": str(e),
            "run_id": run_id,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }


async def shab_daily_status() -> dict[str, Any]:
    """Minimal admin/watchlist status for last successful ingest."""
    async with async_session() as session:
        last = (
            await session.execute(
                select(ShabDailyIngestRun)
                .order_by(ShabDailyIngestRun.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        total_pubs = (
            await session.execute(select(func.count()).select_from(ShabDailyPublication))
        ).scalar_one()
        last_ok = (
            await session.execute(
                select(ShabDailyIngestRun)
                .where(ShabDailyIngestRun.status == "ok")
                .order_by(ShabDailyIngestRun.finished_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    enabled = ingest_enabled()
    if not enabled and last is None:
        hint = "SHAB-Tagesarchiv: aus (SHAB_DAILY_INGEST=0)"
    elif last_ok is None:
        hint = (
            "SHAB-Tagesarchiv: noch kein erfolgreicher Lauf"
            + (f" · letzter Status {last.status}" if last else "")
        )
    else:
        when = last_ok.finished_at or last_ok.started_at
        when_s = when.isoformat(timespec="minutes") if when else "?"
        hint = (
            f"SHAB-Tagesarchiv: letzter Lauf {when_s}"
            f" · Fenster {last_ok.window_start}…{last_ok.window_end}"
            f" · {last_ok.pubs_fetched} Pubs"
            f" · Archiv {int(total_pubs or 0)} gesamt"
        )
        if last_ok.alerts_created:
            hint += f" · {last_ok.alerts_created} Alerts"

    return {
        "enabled": enabled,
        "match_enabled": match_enabled(),
        "total_publications": int(total_pubs or 0),
        "last_run": None
        if last is None
        else {
            "id": last.id,
            "status": last.status,
            "window_start": last.window_start,
            "window_end": last.window_end,
            "started_at": last.started_at.isoformat() if last.started_at else None,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "pubs_fetched": last.pubs_fetched,
            "pubs_upserted": last.pubs_upserted,
            "alerts_created": last.alerts_created,
            "error_message": last.error_message,
        },
        "last_ok": None
        if last_ok is None
        else {
            "id": last_ok.id,
            "window_start": last_ok.window_start,
            "window_end": last_ok.window_end,
            "finished_at": last_ok.finished_at.isoformat() if last_ok.finished_at else None,
            "pubs_fetched": last_ok.pubs_fetched,
        },
        "hint": hint,
        "backfill": "später — siehe backfill_stub / docs/SHAB_DAILY_WATCHLIST.md",
        "retention_note": "unbegrenzt bis Phase-3 Retention-Job (≥90d empfohlen)",
    }


def backfill_stub(*, months: int = 1) -> dict[str, Any]:
    """Placeholder: multi-month historical backfill is a later task."""
    return {
        "implemented": False,
        "months_requested": months,
        "message": (
            "SHAB-Monats-Backfill ist bewusst später. "
            "MVP speichert nur go-forward ab Aktivierung (gestern/heute). "
            "Fehlende Monate später nachziehen — blockiert Phase 1 nicht."
        ),
    }


async def main_cli(argv: list[str] | None = None) -> int:
    """Manual run: ``python -m app.hr_network.shab_daily [--force] [--no-match]``."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if any(a in {"--backfill", "backfill"} for a in args):
        print(backfill_stub())
        return 0
    force = "--force" in args or "-f" in args
    match = "--no-match" not in args
    # Allow one-off even when env off
    result = await run_shab_daily_ingest(force=force or True, match=match)
    print(result)
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_cli()))
