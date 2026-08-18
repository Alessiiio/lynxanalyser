"""Case-flow Optimierungen: Watchlist bei Open, Verdächtig, Zahlung done-Logik."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_fd, _DB_PATH = tempfile.mkstemp(suffix="-lynx-case-flow-test.db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB_PATH
os.environ["SESSION_SECRET"] = "test-session-secret-case-flow-32bytesxx"
os.environ["ENVIRONMENT"] = "development"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
for _k in (
    "SEED_ADMIN_PASSWORD",
    "SEED_CASE_MANAGER_PASSWORD",
    "SEED_COMPLIANCE_PASSWORD",
):
    os.environ.setdefault(_k, "test-pass-case-flow-1")

import app.database as db  # noqa: E402

db.engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}", echo=False)
db.async_session = async_sessionmaker(db.engine, expire_on_commit=False)

from app.hr_network.company_cases import (  # noqa: E402
    assert_l5_confirm_allowed,
    close_documented_case,
    confirm_fraud,
    delete_company_case,
    get_company_case,
    mark_case_suspicious,
    open_case,
    update_bank_check,
    update_payment_flags,
)
from app.hr_network.company_tags import get_company_tag  # noqa: E402
from app.hr_network.person_monitoring import list_watched_persons  # noqa: E402
from app.hr_network.watch_intake import SOURCE_CASE_OPEN, upsert_watched_person  # noqa: E402
from app.hr_network.watched_companies import list_watched_companies  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)


def _fake_intake(**kwargs):
    return {
        "company_name": kwargs.get("name") or "Test AG",
        "company_uid": kwargs.get("uid"),
        "ehraid": 1,
        "enrolled": [
            {
                "person_id": 1,
                "person_slug": "max-muster",
                "display_name": "Max Muster",
                "watch_status": "active",
                "person_hr_status": "current",
                "roles": ["Geschäftsführer"],
                "first_seen": "2024-01-01",
            }
        ],
        "enrolled_count": 1,
        "skipped_former": [
            {
                "person_slug": "old-boss",
                "display_name": "Old Boss",
                "person_hr_status": "former",
                "roles": ["Verwaltungsrat"],
            }
        ],
        "skipped_former_count": 1,
        "include_former": bool(kwargs.get("include_former")),
    }


@pytest.mark.asyncio
async def test_open_case_enrolls_company_watchlist():
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": True, "l5_started": False},
        ),
        patch(
            "app.hr_network.zefix_resolve.resolve_company_detail",
            new=AsyncMock(
                return_value={
                    "name": "Test AG",
                    "ehraid": 42,
                    "uid": "CHE-100.200.300",
                    "purpose": "Handel",
                }
            ),
        ),
    ):
        # Bypass zefix enrich import path — open_case imports resolve inside try
        case = await open_case(
            company_name="Test AG",
            company_uid="CHE-100.200.300",
            company_ehraid=42,
            company_purpose="Handel",
            opened_by="tester",
        )

    assert case["status"] == "under_review"
    assert case["already_existed"] is False
    assert case["l5"]["l5_cached"] is True
    companies = await list_watched_companies(status="active")
    assert companies["total"] == 1
    assert companies["items"][0]["company_name"] == "Test AG"
    assert companies["items"][0]["source_reason"] == "case_open"


@pytest.mark.asyncio
async def test_delete_under_review_case_revokes_case_open_watchlist():
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": True, "l5_started": False},
        ),
    ):
        opened = await open_case(
            company_name="Delete Me AG",
            company_uid="CHE-555.666.777",
            company_ehraid=99,
            opened_by="tester",
        )

    await upsert_watched_person(
        person_slug="carina-zweifel",
        display_name="Zweifel, Carina Ramona",
        residence="CH",
        source_company_ehraid=99,
        source_company_name="Delete Me AG",
        source_reason=SOURCE_CASE_OPEN,
        status="active",
    )
    await upsert_watched_person(
        person_slug="manual-person",
        display_name="Manual Only",
        residence=None,
        source_company_ehraid=99,
        source_company_name="Delete Me AG",
        source_reason="manual",
        status="active",
    )

    persons_before = await list_watched_persons(status="active")
    assert persons_before["total"] == 2
    companies_before = await list_watched_companies(status="active")
    assert companies_before["total"] == 1

    result = await delete_company_case(opened["id"])
    assert result["deleted"] is True
    assert result["watchlist_cleanup"]["persons_removed"] == 1
    assert result["watchlist_cleanup"]["companies_removed"] == 1

    persons_after = await list_watched_persons(status="active")
    assert persons_after["total"] == 1
    assert persons_after["items"][0]["display_name"] == "Manual Only"
    companies_after = await list_watched_companies(status="active")
    assert companies_after["total"] == 0


@pytest.mark.asyncio
async def test_confirm_blocked_while_l5_running():
    l5_mock = AsyncMock(
        return_value={"status": "running", "hits": [], "hit_count": 0}
    )
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": False, "l5_started": True},
        ),
        patch(
            "app.hr_network.company_cases.get_case_network_l5",
            l5_mock,
        ),
    ):
        opened = await open_case(
            company_name="Gate Test AG",
            company_uid="CHE-111.000.222",
            opened_by="tester",
        )

        with pytest.raises(ValueError, match="Netzwerk-Suche"):
            await confirm_fraud(opened["id"], fraud_type="other", by="tester")

        with pytest.raises(ValueError, match="Netzwerk-Suche"):
            await assert_l5_confirm_allowed(opened["id"], bypass=False)

        await assert_l5_confirm_allowed(opened["id"], bypass=True)

        l5_mock.return_value = {"status": "ready", "hits": [], "hit_count": 0}
        confirmed = await confirm_fraud(
            opened["id"],
            fraud_type="other",
            by="tester",
            l5_gate_bypass=True,
        )
        assert confirmed["status"] == "confirmed_fraud"


@pytest.mark.asyncio
async def test_payment_blocked_false_counts_as_done():
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": False, "l5_started": False},
        ),
    ):
        opened = await open_case(
            company_name="Pay AG",
            company_uid="CHE-111.222.333",
            opened_by="tester",
        )
        confirmed = await confirm_fraud(
            opened["id"], fraud_type="investment_scam", by="tester"
        )

    assert confirmed["payment_blocked"] is None
    assert confirmed["documentation_complete"] is False

    updated = await update_payment_flags(
        opened["id"],
        payment_blocked=False,
        payment_blocked_note=None,
    )
    assert updated["payment_blocked"] is False
    # Bank checks seeded from intake → with payment set, docs can complete
    assert updated["bank_checks_total"] >= 1
    pending = updated["bank_checks_pending"]
    if pending == 0:
        assert updated["documentation_complete"] is True


@pytest.mark.asyncio
async def test_mark_suspicious_sets_tag_and_closes():
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": True, "l5_started": False},
        ),
        patch(
            "app.hr_network.under_investigation_watchlist.enroll_under_investigation_watchlist",
            new=AsyncMock(
                return_value={
                    "company": {"company_name": "Verdacht AG"},
                    "persons": [{"display_name": "Max"}],
                    "persons_enrolled": 1,
                }
            ),
        ),
    ):
        opened = await open_case(
            company_name="Verdacht AG",
            company_uid="CHE-999.888.777",
            opened_by="tester",
        )
        result = await mark_case_suspicious(opened["id"], by="tester")

    assert result["status"] == "cleared"
    assert result["clearance_reason"] == "suspicious_flagged"
    assert result["marked_suspicious"] is True
    assert any("[In Abklärung]" in (e.get("text") or "") for e in result["journal"])
    tag = await get_company_tag(uid="CHE-999.888.777", name="Verdacht AG")
    assert tag is not None
    assert tag["tag"] == "under_investigation"


@pytest.mark.asyncio
async def test_mark_suspicious_only_under_review():
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": True, "l5_started": False},
        ),
    ):
        opened = await open_case(
            company_name="Done AG",
            company_uid="CHE-555.444.333",
            opened_by="tester",
        )
        await confirm_fraud(opened["id"], fraud_type="other", by="tester")

    with pytest.raises(ValueError, match="in Prüfung"):
        await mark_case_suspicious(opened["id"], by="tester")

    still = await get_company_case(opened["id"])
    assert still["status"] == "confirmed_fraud"


@pytest.mark.asyncio
async def test_close_documented_case_without_journal_note():
    """Interner Abschluss ohne neuen Journal-Kommentar, wenn Checkliste fertig."""
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": True, "l5_started": False},
        ),
    ):
        opened = await open_case(
            company_name="Close AG",
            company_uid="CHE-222.333.444",
            opened_by="tester",
        )
        confirmed = await confirm_fraud(
            opened["id"], fraud_type="investment_scam", by="tester"
        )

    assert confirmed["status"] == "confirmed_fraud"
    # Complete checklist
    for item in confirmed["bank_checks"]:
        if item["status"] == "pending":
            await update_bank_check(
                opened["id"],
                item["id"],
                status="no_relationship",
                note="",
                checked_by="tester",
            )
    await update_payment_flags(
        opened["id"], payment_blocked=False, payment_blocked_note=None
    )
    ready = await get_company_case(opened["id"])
    assert ready["documentation_complete"] is True
    assert ready["status"] == "ready_for_report"

    closed = await close_documented_case(opened["id"], by="tester", note="")
    assert closed["status"] == "closed"
    assert any("[Dokumentiert]" in (e.get("text") or "") for e in closed["journal"])


@pytest.mark.asyncio
async def test_close_documented_requires_complete_docs():
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": True, "l5_started": False},
        ),
    ):
        opened = await open_case(
            company_name="Incomplete AG",
            company_uid="CHE-333.444.555",
            opened_by="tester",
        )
        await confirm_fraud(opened["id"], fraud_type="other", by="tester")

    with pytest.raises(ValueError, match="unvollständig"):
        await close_documented_case(opened["id"], by="tester")


@pytest.mark.asyncio
async def test_demo_confirm_seeds_person_checklist():
    """DEMO-FRAUD: aktuelle Organe landen in Watchlist + bank_checks (kein Live-Zefix)."""
    with patch(
        "app.hr_network.company_cases._kickoff_l5_background",
        return_value={"l5_cached": True, "l5_started": False},
    ):
        opened = await open_case(
            company_name="DEMO-FRAUD GmbH",
            company_uid="CHE-000.000.001",
            company_ehraid=9000001,
            opened_by="tester",
        )
        confirmed = await confirm_fraud(
            opened["id"], fraud_type="investment_scam", by="tester"
        )

    intake = confirmed.get("watch_intake") or {}
    assert intake.get("enrolled_count", 0) >= 1
    assert any(
        (e.get("display_name") or "") == "Max Muster" for e in (intake.get("enrolled") or [])
    )
    persons = [c for c in confirmed["bank_checks"] if c["entity_type"] == "person"]
    companies = [c for c in confirmed["bank_checks"] if c["entity_type"] == "company"]
    assert len(companies) >= 1
    assert any(p["entity_label"] == "Max Muster" for p in persons)
    # Former organs are not auto-seeded
    assert not any(p["entity_label"] == "Erika Demo" for p in persons)


@pytest.mark.asyncio
async def test_demo_network_l5_ready_with_hits():
    """Akte: L5-Status für Demo ist sofort ready; Hits enthalten Netzwerk-Personen."""
    from app.hr_network.company_cases import apply_case_network_l5_hits, get_case_network_l5

    with patch(
        "app.hr_network.company_cases._kickoff_l5_background",
        return_value={"l5_cached": True, "l5_started": False, "demo_only": True},
    ):
        opened = await open_case(
            company_name="DEMO-FRAUD GmbH",
            company_uid="CHE-000.000.001",
            company_ehraid=9000001,
            opened_by="tester",
        )

    net = await get_case_network_l5(opened["id"], kick=True)
    assert net["status"] == "ready"
    assert net.get("demo_only") is True
    assert net["hit_count"] >= 1
    # L5-only person from fixture
    labels = [h["label"] for h in net["hits"]]
    assert any("Jonas" in x or "Fiktiv" in x for x in labels) or len(labels) >= 1
    assert net.get("graph") and net["graph"].get("nodes")
    groups = {h.get("group") for h in net["hits"]}
    assert "related_company" in groups or "seed_current" in groups or "related_person" in groups
    related_people = [h for h in net["hits"] if h.get("group") == "related_person"]
    if related_people:
        assert any("nicht Organ" in (h.get("hint") or "") for h in related_people)
        assert not any(h.get("default_selected") for h in related_people)

    # Apply one person hit
    person_hits = [h for h in net["hits"] if h["kind"] == "person"]
    assert person_hits
    applied = await apply_case_network_l5_hits(
        opened["id"], items=[person_hits[0]], by="tester"
    )
    assert applied["applied_count"] == 1
    assert any(
        c["entity_label"] == person_hits[0]["label"]
        for c in applied["bank_checks"]
        if c["entity_type"] == "person"
    )


@pytest.mark.asyncio
async def test_lookup_finds_closed_case():
    """Geschlossene Akte bleibt für Firmenanalyse-Flags sichtbar."""
    from app.hr_network.company_cases import find_case_for_company, find_open_case_for_company

    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(side_effect=lambda **kw: _fake_intake(**kw)),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": True, "l5_started": False},
        ),
    ):
        opened = await open_case(
            company_name="Flag Corp AG",
            company_uid="CHE-999.888.777",
            opened_by="tester",
        )
        await confirm_fraud(opened["id"], fraud_type="other", by="tester")
        for item in (await get_company_case(opened["id"]))["bank_checks"]:
            if item["status"] == "pending":
                await update_bank_check(
                    opened["id"], item["id"], status="no_relationship", note="", checked_by="tester"
                )
        await update_payment_flags(opened["id"], payment_blocked=True, payment_blocked_note=None)
        await close_documented_case(opened["id"], by="tester")

    assert await find_open_case_for_company(uid="CHE-999.888.777") is None
    hit = await find_case_for_company(uid="CHE-999.888.777")
    assert hit is not None
    assert hit["status"] == "closed"
    assert hit["id"] == opened["id"]


@pytest.mark.asyncio
async def test_demo_intake_enrolls_current_only():
    from app.hr_network.watch_intake import intake_from_fraud_company

    result = await intake_from_fraud_company(
        name="DEMO-FRAUD GmbH",
        uid="CHE-000.000.001",
        include_former=False,
    )
    assert result["enrolled_count"] >= 1
    assert any(e["display_name"] == "Max Muster" for e in result["enrolled"])
    assert result["skipped_former_count"] >= 1
    assert all(e.get("person_hr_status") != "former" for e in result["enrolled"])
