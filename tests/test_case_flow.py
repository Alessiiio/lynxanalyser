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
    close_documented_case,
    confirm_fraud,
    get_company_case,
    mark_case_suspicious,
    open_case,
    update_bank_check,
    update_payment_flags,
)
from app.hr_network.company_tags import get_company_tag  # noqa: E402
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
    assert any("[Geschlossen]" in (e.get("text") or "") for e in closed["journal"])


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
