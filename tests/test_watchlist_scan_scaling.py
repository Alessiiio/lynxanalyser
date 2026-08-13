"""Watchlist rolling scan: oldest-first batch + coverage."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_fd, _DB_PATH = tempfile.mkstemp(suffix="-lynx-watch-scan-test.db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB_PATH
os.environ["SESSION_SECRET"] = "test-session-secret-watchlist-scan-32bytes"
os.environ["ENVIRONMENT"] = "development"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["WATCHLIST_SCAN_BATCH"] = "25"
os.environ["WATCHLIST_SCAN_DELAY_SEC"] = "0"
os.environ["WATCHLIST_SCAN_MANUAL_LIMIT"] = "5"
for _k in (
    "SEED_ADMIN_PASSWORD",
    "SEED_CASE_MANAGER_PASSWORD",
    "SEED_COMPLIANCE_PASSWORD",
):
    os.environ.setdefault(_k, "test-pass-watchlist-scan-1")

import app.database as db  # noqa: E402

db.engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}", echo=False)
db.async_session = async_sessionmaker(db.engine, expire_on_commit=False)

from app.database import PersonWatchScan, WatchedPerson  # noqa: E402
from app.hr_network.person_monitoring import (  # noqa: E402
    run_person_monitoring,
    select_monitoring_batch,
    select_persons_for_monitoring,
    watchlist_scan_coverage,
)


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)


async def _add_person(
    *,
    name: str,
    slug: str,
    source_reason: str = "manual",
    status: str = "active",
    scan_priority: str = "normal",
    added_at: datetime | None = None,
    last_run: datetime | None = None,
) -> int:
    async with db.async_session() as session:
        p = WatchedPerson(
            person_slug=slug,
            display_name=name,
            source_reason=source_reason,
            status=status,
            scan_priority=scan_priority,
            added_at=added_at or datetime.now(timezone.utc),
        )
        session.add(p)
        await session.flush()
        if last_run is not None:
            session.add(
                PersonWatchScan(
                    person_id=p.id,
                    last_scanned_month="2026-01",
                    last_run_at=last_run,
                )
            )
        await session.commit()
        return p.id


@pytest.mark.asyncio
async def test_select_oldest_and_never_scanned_first():
    now = datetime.now(timezone.utc)
    old_id = await _add_person(
        name="Alt, Anna",
        slug="alt-anna",
        last_run=now - timedelta(days=30),
    )
    new_id = await _add_person(
        name="Neu, Nina",
        slug="neu-nina",
        last_run=now - timedelta(days=1),
    )
    never_id = await _add_person(name="Nie, Nora", slug="nie-nora", last_run=None)
    cleared = await _add_person(
        name="Clear, Carl",
        slug="clear-carl",
        status="cleared",
        last_run=None,
    )

    picked = await select_persons_for_monitoring(limit=3)
    ids = [p.id for p in picked]
    assert never_id in ids
    assert ids[0] == never_id
    assert old_id in ids
    assert new_id in ids
    assert cleared not in ids
    # After never-scanned, oldest last_run first
    assert ids.index(old_id) < ids.index(new_id)


@pytest.mark.asyncio
async def test_under_investigation_preferred_among_never_scanned():
    await _add_person(
        name="Manual, Max",
        slug="manual-max",
        source_reason="manual",
        last_run=None,
    )
    ui_id = await _add_person(
        name="Abklaerung, Ada",
        slug="abklaerung-ada",
        source_reason="under_investigation",
        scan_priority="high",
        last_run=None,
    )
    picked = await select_persons_for_monitoring(limit=1)
    assert picked[0].id == ui_id


@pytest.mark.asyncio
async def test_high_priority_scanned_before_rolling_rest():
    now = datetime.now(timezone.utc)
    # High was scanned recently — still must be selected in high tier
    high_id = await _add_person(
        name="Fall, Fred",
        slug="fall-fred",
        source_reason="case_open",
        scan_priority="high",
        last_run=now - timedelta(hours=1),
    )
    # Normal never scanned — would win pure rolling, but comes after high
    normal_id = await _add_person(
        name="Normal, Nora",
        slug="normal-nora",
        source_reason="manual",
        scan_priority="normal",
        last_run=None,
    )
    people, meta = await select_monitoring_batch(
        rolling_limit=5,
        high_priority_cap=50,
    )
    ids = [p.id for p in people]
    assert high_id in ids
    assert normal_id in ids
    assert ids.index(high_id) < ids.index(normal_id)
    assert meta["high_priority_selected"] == 1
    assert meta["rolling_selected"] == 1


@pytest.mark.asyncio
async def test_high_priority_only_skips_normal():
    await _add_person(
        name="High, Hans",
        slug="high-hans",
        source_reason="fraud_list_officer",
        scan_priority="high",
        last_run=None,
    )
    await _add_person(
        name="Low, Lisa",
        slug="low-lisa",
        source_reason="manual",
        scan_priority="normal",
        last_run=None,
    )
    people, meta = await select_monitoring_batch(
        rolling_limit=10,
        high_priority_cap=50,
        high_priority_only=True,
    )
    assert len(people) == 1
    assert people[0].display_name == "High, Hans"
    assert meta["high_priority_only"] is True
    assert meta["rolling_selected"] == 0


@pytest.mark.asyncio
async def test_cron_style_batch_includes_high_and_rolling():
    await _add_person(
        name="H",
        slug="h",
        scan_priority="high",
        source_reason="case_open",
        last_run=None,
    )
    await _add_person(
        name="R",
        slug="r",
        scan_priority="normal",
        last_run=None,
    )

    scanned: list[int] = []

    async def fake_scan(pid, **kwargs):
        scanned.append(pid)
        return {
            "person_id": pid,
            "display_name": str(pid),
            "new_links": 0,
            "alerts": 0,
            "created_alerts": [],
        }

    with (
        patch(
            "app.hr_network.person_monitoring.scan_watched_person_incremental",
            new_callable=AsyncMock,
            side_effect=fake_scan,
        ),
        patch("app.notify_email.notify_watchlist_new_hits") as notify,
    ):
        result = await run_person_monitoring(
            limit=5,
            delay_sec=0,
            include_high_priority=True,
            high_priority_cap=50,
        )

    assert result["scanned"] == 2
    assert result["selection"]["high_priority_selected"] == 1
    assert result["selection"]["rolling_selected"] == 1
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_coverage_counts():
    now = datetime.now(timezone.utc)
    await _add_person(name="A", slug="a", last_run=now)
    await _add_person(name="B", slug="b", last_run=now - timedelta(days=20))
    await _add_person(name="C", slug="c", last_run=None)
    cov = await watchlist_scan_coverage(window_days=7)
    assert cov["total_monitorable"] == 3
    assert cov["scanned_within_window"] == 1
    assert cov["never_scanned"] == 1
    assert cov["coverage_pct"] == pytest.approx(33.3, abs=0.1)


@pytest.mark.asyncio
async def test_batch_digest_once_not_per_person():
    await _add_person(name="P1", slug="p1", last_run=None)
    await _add_person(name="P2", slug="p2", last_run=None)

    async def fake_scan(pid, **kwargs):
        return {
            "person_id": pid,
            "display_name": f"P{pid}",
            "new_links": 1,
            "alerts": 1,
            "created_alerts": [
                {
                    "alert_type": "new_role",
                    "severity": "medium",
                    "company_name": f"Firma {pid}",
                    "confidence": "high",
                    "source": "moneyhouse",
                    "message": "hit",
                }
            ],
        }

    with (
        patch(
            "app.hr_network.person_monitoring.scan_watched_person_incremental",
            new_callable=AsyncMock,
            side_effect=fake_scan,
        ),
        patch(
            "app.notify_email.notify_watchlist_new_hits",
            return_value={"sent": True, "alert_count": 2},
        ) as notify,
    ):
        result = await run_person_monitoring(limit=2, delay_sec=0)

    assert result["scanned"] == 2
    assert result["alerts"] == 2
    assert result["concurrency"] == 1
    notify.assert_called_once()
    assert len(notify.call_args.args[0]) == 2
    assert notify.call_args.kwargs.get("source") == "batch_monitoring"


@pytest.mark.asyncio
async def test_batch_no_email_without_alerts():
    await _add_person(name="Quiet", slug="quiet", last_run=None)

    async def fake_scan(pid, **kwargs):
        return {
            "person_id": pid,
            "display_name": "Quiet",
            "new_links": 0,
            "alerts": 0,
            "created_alerts": [],
        }

    with (
        patch(
            "app.hr_network.person_monitoring.scan_watched_person_incremental",
            new_callable=AsyncMock,
            side_effect=fake_scan,
        ),
        patch("app.notify_email.notify_watchlist_new_hits") as notify,
    ):
        result = await run_person_monitoring(limit=1, delay_sec=0)

    notify.assert_not_called()
    assert result["email"]["reason"] == "no_alerts"
