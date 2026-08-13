"""SHAB daily ingest: flatten + idempotent upsert by shab_id."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_fd, _DB_PATH = tempfile.mkstemp(suffix="-lynx-shab-daily-test.db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB_PATH
os.environ["SESSION_SECRET"] = "test-session-secret-shab-daily-32bytesxx"
os.environ["ENVIRONMENT"] = "development"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["SHAB_DAILY_INGEST"] = "1"
os.environ["SHAB_DAILY_MATCH"] = "0"
for _k in (
    "SEED_ADMIN_PASSWORD",
    "SEED_CASE_MANAGER_PASSWORD",
    "SEED_COMPLIANCE_PASSWORD",
):
    os.environ.setdefault(_k, "test-pass-shab-daily-xxx1")

import app.database as db  # noqa: E402

db.engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}", echo=False)
db.async_session = async_sessionmaker(db.engine, expire_on_commit=False)

from app.database import NetworkAlert, ShabDailyPublication, WatchedPerson  # noqa: E402
from app.hr_network import shab_daily as sd  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)


def _sample_api_page(*, shab_id: int = 1006481391, message: str = "Eingetragene Personen: Muster, Max, von Bern, Verwaltungsrat") -> dict:
    return {
        "list": [
            {
                "name": "Beispiel AG",
                "uid": "CHE123456789",
                "uidFormatted": "CHE-123.456.789",
                "ehraid": 42,
                "registerOfficeId": 20,
                "shabPub": [
                    {
                        "shabId": shab_id,
                        "shabDate": "2026-08-12",
                        "message": message,
                        "mutationTypes": [{"key": "aenderungorgane"}],
                        "registryOfficeCanton": "ZH",
                        "registryOfficeId": 20,
                        "registryOfficeJournalId": 99,
                        "registryOfficeJournalDate": "2026-08-11",
                    }
                ],
            }
        ],
        "hasMoreResults": False,
        "maxOffset": 0,
    }


def test_flatten_extracts_shab_id_and_canton():
    rows = sd.flatten_shab_search_page(_sample_api_page())
    assert len(rows) == 1
    assert rows[0]["shab_id"] == "1006481391"
    assert rows[0]["canton"] == "ZH"
    assert rows[0]["company_ehraid"] == 42
    assert "aenderungorgane" in rows[0]["mutation_types"]
    assert any(p.get("name") == "Muster, Max" for p in rows[0]["person_names"])


@pytest.mark.asyncio
async def test_upsert_idempotent_by_shab_id():
    page1 = _sample_api_page(message="Eingetragene Personen: Muster, Max, von Bern, Verwaltungsrat")
    rows1 = sd.flatten_shab_search_page(page1)
    stats1 = await sd.upsert_publications(rows1)
    assert stats1["inserted"] == 1
    assert stats1["updated"] == 0

    page2 = _sample_api_page(
        message="Eingetragene Personen: Muster, Max, von Bern, Präsident und Verwaltungsrat"
    )
    rows2 = sd.flatten_shab_search_page(page2)
    stats2 = await sd.upsert_publications(rows2)
    assert stats2["inserted"] == 0
    assert stats2["updated"] == 1
    assert stats2["upserted"] == 1

    async with db.async_session() as session:
        count = (
            await session.execute(select(func.count()).select_from(ShabDailyPublication))
        ).scalar_one()
        row = (
            await session.execute(
                select(ShabDailyPublication).where(
                    ShabDailyPublication.shab_id == "1006481391"
                )
            )
        ).scalar_one()
    assert int(count) == 1
    assert "Präsident" in (row.message or "")


@pytest.mark.asyncio
async def test_ingest_run_idempotent_with_mocked_api():
    calls = {"n": 0}

    def fake_post(path: str, payload: dict) -> dict:
        calls["n"] += 1
        assert path == "/shab/search.json"
        assert "registryOffices" not in payload  # CH-wide
        return _sample_api_page()

    with patch.object(sd, "zefix_rest_post", side_effect=fake_post):
        r1 = await sd.run_shab_daily_ingest(force=True, match=False)
        r2 = await sd.run_shab_daily_ingest(force=True, match=False)

    assert r1.get("error") is None
    assert r2.get("error") is None
    assert r1["fetched"] == 1
    assert r2["upsert"]["inserted"] == 0
    assert r2["upsert"]["updated"] == 1
    assert calls["n"] >= 2

    async with db.async_session() as session:
        count = (
            await session.execute(select(func.count()).select_from(ShabDailyPublication))
        ).scalar_one()
    assert int(count) == 1


@pytest.mark.asyncio
async def test_match_creates_alert_once():
    rows = sd.flatten_shab_search_page(_sample_api_page())
    await sd.upsert_publications(rows)

    async with db.async_session() as session:
        session.add(
            WatchedPerson(
                person_slug="muster-max",
                display_name="Muster, Max",
                residence="Bern",
                source_reason="manual",
                status="active",
            )
        )
        await session.commit()

    m1 = await sd.match_publications_against_watchlist(
        publication_dates=["2026-08-12"]
    )
    m2 = await sd.match_publications_against_watchlist(
        publication_dates=["2026-08-12"]
    )
    assert m1["alerts"] == 1
    assert m2["alerts"] == 0
    assert m2["skipped_existing"] >= 1


@pytest.mark.asyncio
async def test_neueintragung_watched_organ_creates_hit():
    """D9-style smoke: watchlist organ + synthetic Neueintragung → NetworkAlert."""
    msg = (
        "Neueintragung.\n\n"
        "Eingetragene Personen: Barbul, Michael, von Zürich, in Zürich, "
        "Inhaber und Geschäftsführer."
    )
    page = _sample_api_page(shab_id=1007000001, message=msg)
    # Override company for clarity
    page["list"][0]["name"] = "Neue Holding AG"
    page["list"][0]["ehraid"] = 9001
    page["list"][0]["shabPub"][0]["mutationTypes"] = [{"key": "status.neu"}]
    page["list"][0]["shabPub"][0]["shabDate"] = "2026-08-20"

    rows = sd.flatten_shab_search_page(page)
    assert any(p.get("name") == "Barbul, Michael" for p in rows[0]["person_names"])
    await sd.upsert_publications(rows)

    async with db.async_session() as session:
        session.add(
            WatchedPerson(
                person_slug="barbul-michael",
                display_name="Barbul, Michael",
                residence="Zürich",
                source_reason="under_investigation",
                status="active",
                scan_priority="high",
            )
        )
        await session.commit()

    result = await sd.match_publications_against_watchlist(
        publication_dates=["2026-08-20"]
    )
    assert result["alerts"] == 1
    assert result["new_links"] == 1

    async with db.async_session() as session:
        alert = (await session.execute(select(NetworkAlert))).scalar_one()
    assert alert.alert_type == "new_company_founded"
    assert alert.severity == "high"
    assert "shab_daily" in (alert.message or "")
    assert "Neue Holding AG" in (alert.message or "")
    assert "Barbul, Michael" in (alert.message or "")


def test_backfill_stub_not_implemented():
    stub = sd.backfill_stub(months=6)
    assert stub["implemented"] is False
    assert "später" in stub["message"].lower() or "später" in stub["message"]
