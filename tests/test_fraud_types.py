"""Betrugsarten-Taxonomie + Mapping legacy → Broschüre."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_fd, _DB_PATH = tempfile.mkstemp(suffix="-lynx-fraud-types-test.db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB_PATH
os.environ["SESSION_SECRET"] = "test-session-secret-fraud-types-32bytesx"
os.environ["ENVIRONMENT"] = "development"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
for _k in (
    "SEED_ADMIN_PASSWORD",
    "SEED_CASE_MANAGER_PASSWORD",
    "SEED_COMPLIANCE_PASSWORD",
):
    os.environ.setdefault(_k, "test-pass-fraud-types-1")

import app.database as db  # noqa: E402

db.engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}", echo=False)
db.async_session = async_sessionmaker(db.engine, expire_on_commit=False)

from app.hr_network import company_cases as company_cases_mod  # noqa: E402
from app.hr_network import watched_companies as watched_companies_mod  # noqa: E402
from app.hr_network.company_cases import (  # noqa: E402
    confirm_fraud,
    export_flagged_company_names_csv,
    export_fraud_companies_csv,
    open_case,
)
from app.hr_network.fraud_types import (  # noqa: E402
    FRAUD_TYPES,
    fraud_type_label,
    is_valid_fraud_type,
    normalize_fraud_type,
)

# Keep module-bound sessions in sync if another test file imported first.
company_cases_mod.async_session = db.async_session
watched_companies_mod.async_session = db.async_session


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    company_cases_mod.async_session = db.async_session
    watched_companies_mod.async_session = db.async_session
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)


def test_normalize_fake_bank_employee():
    assert normalize_fraud_type("fake_bank_employee") == "phone_scam"
    assert is_valid_fraud_type("fake_bank_employee")
    assert is_valid_fraud_type("phone_scam")
    assert fraud_type_label("fake_bank_employee") == "Telefonbetrug"
    assert fraud_type_label("phone_scam") == "Telefonbetrug"
    assert "ceo_scam" in FRAUD_TYPES
    assert "grandchild_scam" in FRAUD_TYPES


@contextmanager
def _case_patches():
    with (
        patch(
            "app.hr_network.watch_intake.intake_from_fraud_company",
            new=AsyncMock(
                return_value={
                    "enrolled": [],
                    "enrolled_count": 0,
                    "skipped_former": [],
                    "skipped_former_count": 0,
                }
            ),
        ),
        patch(
            "app.hr_network.company_cases._kickoff_l5_background",
            return_value={"l5_cached": True, "l5_started": False},
        ),
        patch(
            "app.hr_network.company_cases.get_case_network_l5",
            new=AsyncMock(return_value={"status": "ready", "hits": [], "hit_count": 0}),
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_confirm_accepts_brochure_type_and_legacy_alias():
    with _case_patches():
        opened = await open_case(
            company_name="Phone Scam AG",
            company_uid="CHE-111.222.333",
            opened_by="tester",
        )
        confirmed = await confirm_fraud(
            opened["id"], fraud_type="fake_bank_employee", by="tester"
        )
    assert confirmed["fraud_type"] == "phone_scam"
    assert confirmed["fraud_type_label"] == "Telefonbetrug"

    with _case_patches():
        opened2 = await open_case(
            company_name="CEO Corp AG",
            company_uid="CHE-444.555.666",
            opened_by="tester",
        )
        confirmed2 = await confirm_fraud(
            opened2["id"], fraud_type="ceo_scam", by="tester"
        )
    assert confirmed2["fraud_type"] == "ceo_scam"
    assert confirmed2["fraud_type_label"] == "CEO Scam"


@pytest.mark.asyncio
async def test_fraud_csv_uses_german_label():
    with _case_patches():
        opened = await open_case(
            company_name="Invest AG",
            company_uid="CHE-777.888.999",
            opened_by="tester",
        )
        await confirm_fraud(opened["id"], fraud_type="investment_scam", by="tester")

    csv_text = await export_fraud_companies_csv()
    assert "Investment Scam" in csv_text
    data_rows = [ln for ln in csv_text.strip().splitlines()[1:] if ln]
    assert data_rows
    assert "investment_scam" not in data_rows[0]


@pytest.mark.asyncio
async def test_ds_name_export_contains_confirmed_company_only_name():
    with _case_patches():
        opened = await open_case(
            company_name="AB Suspect GmbH",
            company_uid="CHE-100.200.300",
            opened_by="tester",
        )
        await confirm_fraud(opened["id"], fraud_type="refund_scam", by="tester")

    csv_text = await export_flagged_company_names_csv()
    lines = [ln for ln in csv_text.strip().splitlines() if ln]
    assert lines[0] == "Firmenname"
    assert "AB Suspect GmbH" in lines
    assert ";" not in lines[1]
