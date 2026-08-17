"""Watchlist Firmen-Dedup (A) + In Abklärung → Watchlist."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_fd, _DB_PATH = tempfile.mkstemp(suffix="-lynx-watchlist-test.db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB_PATH
os.environ["SESSION_SECRET"] = "test-session-secret-watchlist-bulk-32bytes"
os.environ["ENVIRONMENT"] = "development"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
for _k in (
    "SEED_ADMIN_PASSWORD",
    "SEED_CASE_MANAGER_PASSWORD",
    "SEED_COMPLIANCE_PASSWORD",
):
    os.environ.setdefault(_k, "test-pass-watchlist-1")

import app.database as db  # noqa: E402

db.engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}", echo=False)
db.async_session = async_sessionmaker(db.engine, expire_on_commit=False)

from app.database import WatchedCompany  # noqa: E402
from app.hr_network.company_tags import (  # noqa: E402
    TAG_UNDER_INVESTIGATION,
    clear_company_tag,
    set_company_tag,
)
from app.hr_network.watched_companies import (  # noqa: E402
    find_watched_company_match,
    list_watched_companies,
    upsert_watched_company,
)


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_company_dedup_uid_primary():
    first = await upsert_watched_company(
        company_name="Alpha AG",
        company_uid="CHE-123.456.789",
        address="Bahnhofstrasse 1, 8001 Zürich",
        source_reason="manual",
        added_by="Tester",
    )
    second = await upsert_watched_company(
        company_name="Alpha AG Neu",
        company_uid="CHE123456789",
        address="Neue Strasse 2, 8001 Zürich",
        source_reason="bulk_scan",
        added_by="Tester",
    )
    assert first["id"] == second["id"]
    assert second["already_existed"] is True
    assert second["company_name"] == "Alpha AG Neu"
    assert "Neue Strasse" in (second["address"] or "")
    listed = await list_watched_companies(status="active")
    assert listed["total"] == 1


@pytest.mark.asyncio
async def test_company_dedup_name_fallback():
    first = await upsert_watched_company(
        company_name="Beta GmbH",
        company_uid=None,
        source_reason="manual",
    )
    second = await upsert_watched_company(
        company_name="  beta   gmbh ",
        company_uid="CHE-999.888.777",
        address="Sitz Bern",
        source_reason="under_investigation",
    )
    assert first["id"] == second["id"]
    digits = "".join(c for c in (second["company_uid"] or "") if c.isdigit())
    assert digits == "999888777"
    listed = await list_watched_companies(status="active")
    assert listed["total"] == 1


def test_find_match_uid_beats_name():
    rows = [
        WatchedCompany(id=1, company_name="Other SA", company_uid="CHE-111.111.111"),
        WatchedCompany(id=2, company_name="Target AG", company_uid="CHE-222.222.222"),
    ]
    hit = find_watched_company_match(rows, uid="CHE222222222", name="Other SA")
    assert hit is not None
    assert hit.id == 2


@pytest.mark.asyncio
async def test_in_abklaerung_enrolls_company_and_organs():
    fake_persons = [
        {
            "name": "Muster, Max",
            "residence": "Zürich",
            "roles": ["Präsident"],
            "status": "current",
        },
        {
            "name": "Alt, Anna",
            "residence": "Bern",
            "roles": ["Mitglied"],
            "status": "former",
        },
    ]

    with patch(
        "app.hr_network.under_investigation_watchlist._fetch_current_organs_l2",
        new_callable=AsyncMock,
    ) as mock_l2:
        mock_l2.return_value = ({}, [])
        from app.hr_network.under_investigation_watchlist import (
            enroll_under_investigation_watchlist,
        )

        result = await enroll_under_investigation_watchlist(
            company_name="Gamma AG",
            company_uid="CHE-321.654.987",
            address="Testweg 3, 3000 Bern",
            legal_seat="Bern",
            company_ehraid=42,
            added_by="Case Manager",
            persons=fake_persons,
        )

    assert result["company"]["company_name"] == "Gamma AG"
    assert result["persons_enrolled"] == 1
    assert result["persons"][0]["display_name"] == "Muster, Max"
    mock_l2.assert_not_called()

    companies = await list_watched_companies(status="active")
    assert companies["total"] == 1
    assert companies["items"][0]["source_reason"] == "under_investigation"

    from app.hr_network.person_monitoring import list_watched_persons

    people = await list_watched_persons(status="active", limit=50, offset=0)
    assert people["total"] >= 1
    names = {p["display_name"] for p in people["items"]}
    assert "Muster, Max" in names
    assert "Alt, Anna" not in names
    muster = next(p for p in people["items"] if p["display_name"] == "Muster, Max")
    assert muster.get("scan_priority") == "high"


@pytest.mark.asyncio
async def test_clear_tag_keeps_watchlist():
    await upsert_watched_company(
        company_name="Delta AG",
        company_uid="CHE-555.555.555",
        source_reason="under_investigation",
    )
    await set_company_tag(
        company_name="Delta AG",
        company_uid="CHE-555.555.555",
        set_by="Tester",
        tag=TAG_UNDER_INVESTIGATION,
    )
    cleared = await clear_company_tag(
        uid="CHE-555.555.555",
        name="Delta AG",
        tag=TAG_UNDER_INVESTIGATION,
    )
    assert cleared is True
    listed = await list_watched_companies(status="active")
    assert listed["total"] == 1
    assert listed["items"][0]["company_name"] == "Delta AG"


@pytest.mark.asyncio
async def test_in_abklaerung_fetches_l2_when_no_persons():
    from app.hr_network.under_investigation_watchlist import (
        enroll_under_investigation_watchlist,
    )

    with patch(
        "app.hr_network.under_investigation_watchlist._fetch_current_organs_l2",
        new_callable=AsyncMock,
        return_value=(
            {
                "name": "Epsilon AG",
                "uid": "CHE-777.777.777",
                "address": "Weg 1",
                "legal_seat": "ZH",
                "ehraid": 7,
            },
            [{"name": "Organ, Oli", "residence": "ZH", "roles": ["VR"]}],
        ),
    ):
        out = await enroll_under_investigation_watchlist(
            company_name="Epsilon AG",
            company_uid="CHE-777.777.777",
            added_by="Admin",
            persons=None,
        )
    assert out["persons_enrolled"] == 1
    companies = await list_watched_companies(status="active", q="Epsilon")
    assert companies["total"] == 1


def test_compact_scan_includes_graph_and_via():
    from app.hr_network.bulk_scan import _compact_scan_result

    data = {
        "seed_companies": [{"name": "Alpha AG", "uid": "CHE-1", "address": "ZH"}],
        "persons_table": [
            {"name": "Muster, Max", "residence": "Bern", "roles": ["VR"], "status": "current"}
        ],
        "nodes": [
            {"id": "c1", "type": "company", "label": "Alpha AG", "uid": "CHE-1", "is_seed": True},
            {
                "id": "c2",
                "type": "company",
                "label": "Beta GmbH",
                "uid": "CHE-2",
                "is_seed": False,
            },
            {
                "id": "p1",
                "type": "person",
                "label": "Muster, Max",
                "person_status": "current",
                "roles": ["VR"],
            },
        ],
        "edges": [
            {"from": "p1", "to": "c1", "label": "VR", "person_status": "current"},
            {"from": "p1", "to": "c2", "label": "GF", "person_status": "current"},
        ],
        "stats": {"node_count": 3},
    }
    compact = _compact_scan_result(data)
    assert compact["company"]["name"] == "Alpha AG"
    assert compact["graph"]["nodes"]
    assert compact["graph"]["edges"]
    related = compact["related_companies"]
    assert len(related) == 1
    assert related[0]["name"] == "Beta GmbH"
    assert "Muster, Max" in related[0]["via"]


def test_source_label_plain_language():
    from app.hr_network.watched_companies import source_label

    assert source_label("bulk_scan") == "Scan"
    assert source_label("under_investigation") == "Abklärung"
    assert source_label("case_open") == "Fall"
    assert source_label("manual") == "Manuell"
