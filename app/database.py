from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import config
from app.models import FullReport

logger = logging.getLogger(__name__)


def _database_url() -> str:
    path = os.path.abspath(config.DATABASE_PATH)
    return f"sqlite+aiosqlite:///{path}"


engine = create_async_engine(_database_url(), echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class ScanHistory(Base):
    __tablename__ = "scan_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    company_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    transaction_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    transaction_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    transaction_purpose: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    total_score: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(64))
    critical_flags: Mapped[list] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    checked_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, default="unknown")

    check_details: Mapped[list["CheckDetail"]] = relationship(
        back_populates="scan",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_scan_history_domain_checked_at", "domain", "checked_at"),)


class CheckDetail(Base):
    __tablename__ = "check_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scan_history.id"), index=True)
    check_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    score: Mapped[int] = mapped_column(Integer)
    max_score: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(1024))
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)

    scan: Mapped["ScanHistory"] = relationship(back_populates="check_details")


class AnalystFeedback(Base):
    __tablename__ = "analyst_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    feedback_text: Mapped[str] = mapped_column(String(2048), default="")
    action: Mapped[str] = mapped_column(String(64), default="dismiss_category")
    original_fraud_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    analyst_id: Mapped[str] = mapped_column(String(128), default="unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class WatchedPerson(Base):
    __tablename__ = "watched_persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_slug: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    residence: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_company_ehraid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_company_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_reason: Mapped[str] = mapped_column(String(64))
    # active | low_priority | cleared | confirmed_fraud
    status: Mapped[str] = mapped_column(String(32), default="active")
    # high = fall/In-Abklärung — nightly scan before rolling queue; normal = rolling only
    scan_priority: Mapped[str] = mapped_column(String(16), default="normal", index=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    case_notes: Mapped[Optional[str]] = mapped_column(String(4000), nullable=True)
    flag_undesired_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_aml: Mapped[bool] = mapped_column(Boolean, default=False)

    company_links: Mapped[list["PersonCompanyLink"]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
    )
    watch_scans: Mapped[list["PersonWatchScan"]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
    )


class PersonCompanyLink(Base):
    __tablename__ = "person_company_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("watched_persons.id"), index=True)
    company_ehraid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    company_name: Mapped[str] = mapped_column(String(512))
    company_uid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # seed | existing | newly_found
    relation_type: Mapped[str] = mapped_column(String(32))
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    is_seed_company: Mapped[bool] = mapped_column(Boolean, default=False)
    match_confidence: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    person: Mapped["WatchedPerson"] = relationship(back_populates="company_links")


class PersonWatchScan(Base):
    __tablename__ = "person_watch_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("watched_persons.id"), index=True)
    last_scanned_month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    last_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    person: Mapped["WatchedPerson"] = relationship(back_populates="watch_scans")


class NetworkAlert(Base):
    __tablename__ = "network_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(64), index=True)
    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("watched_persons.id"), nullable=True, index=True
    )
    company_ehraid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))  # case_manager | compliance | admin
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    totp_secret_encrypted: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    backup_codes_hash: Mapped[Optional[str]] = mapped_column(String(4096), nullable=True)
    backup_codes_generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WatchedPersonStatusHistory(Base):
    __tablename__ = "watched_person_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("watched_persons.id"), index=True)
    old_status: Mapped[str] = mapped_column(String(32))
    new_status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(1024))
    changed_by: Mapped[str] = mapped_column(String(128))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class ShabDailyPublication(Base):
    """Go-forward SHAB/SOGC archive (CH-wide daily ingest). Keyed by shab_id."""

    __tablename__ = "shab_daily_publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shab_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    publication_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, index=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    company_uid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    company_ehraid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    canton: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)
    registry_office_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mutation_types: Mapped[list] = mapped_column(JSON, default=list)
    message: Mapped[str] = mapped_column(Text, default="")
    person_names: Mapped[list] = mapped_column(JSON, default=list)
    journal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    journal_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_shab_daily_pub_date_canton", "publication_date", "canton"),
    )


class ShabDailyIngestRun(Base):
    """One fetch/upsert attempt for a publication-date window."""

    __tablename__ = "shab_daily_ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    window_start: Mapped[str] = mapped_column(String(10))
    window_end: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error
    pubs_fetched: Mapped[int] = mapped_column(Integer, default=0)
    pubs_upserted: Mapped[int] = mapped_column(Integer, default=0)
    pubs_inserted: Mapped[int] = mapped_column(Integer, default=0)
    alerts_created: Mapped[int] = mapped_column(Integer, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    ch_wide: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)


class ShabDailyMatch(Base):
    """Idempotent watchlist match log (shab_id × person)."""

    __tablename__ = "shab_daily_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shab_id: Mapped[str] = mapped_column(String(64), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("watched_persons.id"), index=True)
    company_ehraid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    matched_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    alert_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("shab_id", "person_id", name="uq_shab_daily_match_pub_person"),
    )


class CompanyCase(Base):
    """Central case lifecycle for a beneficiary company."""

    __tablename__ = "company_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_ehraid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    company_name: Mapped[str] = mapped_column(String(512))
    company_uid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    company_purpose: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    fraud_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # under_review | confirmed_fraud | cleared | ready_for_report | reported | closed
    status: Mapped[str] = mapped_column(String(32), default="under_review", index=True)
    opened_by: Mapped[str] = mapped_column(String(128))
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_blocked: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    payment_blocked_note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Optional payment-hit context (no customer PII — amount/ref from the alert only)
    hit_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hit_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    hit_reference: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    hit_note: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    report_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    reported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    compliance_actioned_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    compliance_actioned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    compliance_note: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    snapshot_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    source_alert_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("network_alerts.id"), nullable=True, index=True
    )


class CaseJournalEntry(Base):
    __tablename__ = "case_journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_cases.id"), index=True)
    author: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    text: Mapped[str] = mapped_column(String(4000))


class CaseBankCheckItem(Base):
    __tablename__ = "case_bank_check_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("company_cases.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))  # company | person
    entity_label: Mapped[str] = mapped_column(String(512))
    entity_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending | no_relationship | relationship_found
    checked_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)


class AppSetting(Base):
    """Key/value admin settings (e.g. anonymize_mode)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class CompanySearchHistory(Base):
    """Shared team log of Firmenanalyse queries (start-page recent searches)."""

    __tablename__ = "company_search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(512), default="")
    company_uid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    searched_by: Mapped[str] = mapped_column(String(128), default="Team")
    searched_by_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    searched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    __table_args__ = (
        Index("ix_company_search_history_uid_at", "company_uid", "searched_at"),
    )


class CompanyTag(Base):
    """Lightweight team firm tags (e.g. «In Abklärung») — not a formal Akte."""

    __tablename__ = "company_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(512), default="")
    company_uid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    # MVP: under_investigation («In Abklärung»)
    tag: Mapped[str] = mapped_column(String(64), default="under_investigation", index=True)
    set_by: Mapped[str] = mapped_column(String(128), default="Team")
    set_by_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    __table_args__ = (
        Index("ix_company_tags_uid_tag", "company_uid", "tag"),
        Index("ix_company_tags_tag_at", "tag", "set_at"),
    )


class WatchedCompany(Base):
    """Firmen-Watchlist (getrennt von Tags und Fall-Akte)."""

    __tablename__ = "watched_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(512), default="")
    company_uid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    company_ehraid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    address: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    legal_seat: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # bulk_scan | under_investigation | manual
    source_reason: Mapped[str] = mapped_column(String(64), default="manual")
    # active | cleared
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    added_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    __table_args__ = (
        Index("ix_watched_companies_uid_status", "company_uid", "status"),
        Index("ix_watched_companies_name", "company_name"),
    )


class BulkScanJob(Base):
    """Async bulk firm scan (Admin) — paste names → progress → Auswahl."""

    __tablename__ = "bulk_scan_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by: Mapped[str] = mapped_column(String(128), default="Admin")
    created_by_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    # pending | running | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    level: Mapped[int] = mapped_column(Integer, default=3)
    options_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BulkScanItem(Base):
    __tablename__ = "bulk_scan_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("bulk_scan_jobs.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    input_name: Mapped[str] = mapped_column(String(512), default="")
    # pending | running | matched | ambiguous | not_found | error
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    resolved_uid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    resolved_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    legal_seat: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ehraid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)


class AuditEvent(Base):
    """Admin-readable audit trail (logins, user admin, exports, …)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    # login_ok | login_fail | logout | 2fa_ok | 2fa_fail | user_* | export_* | setting_* | …
    action: Mapped[str] = mapped_column(String(64), index=True)
    actor_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    actor_display: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    target: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_audit_events_action_at", "action", "created_at"),
    )


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_drop_legacy_bank_tables)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_scan_history_columns)
        await conn.run_sync(_migrate_watched_person_columns)
        await conn.run_sync(_migrate_company_case_columns)
        await conn.run_sync(_migrate_user_2fa_columns)
    await seed_default_users()


async def seed_default_users() -> None:
    """Create bootstrap accounts if empty; migrate roles; optionally reset passwords."""
    from app.auth import hash_password, normalize_role

    seeds = [
        ("admin", config.SEED_ADMIN_PASSWORD, "Administrator", "admin"),
        ("case_manager", config.SEED_CASE_MANAGER_PASSWORD, "Case Manager", "case_manager"),
        ("compliance", config.SEED_COMPLIANCE_PASSWORD, "Compliance", "compliance"),
    ]
    if not all(password for _, password, _, _ in seeds):
        if not config.IS_PRODUCTION:
            logger.warning("SEED_*_PASSWORD nicht gesetzt — keine Bootstrap-User erstellt")
        await _migrate_legacy_roles()
        await _ensure_alessio_admin()
        return

    async with async_session() as session:
        existing_users = list((await session.execute(select(User))).scalars().all())
        by_username = {u.username: u for u in existing_users}

        if not existing_users:
            for username, password, display, role in seeds:
                session.add(
                    User(
                        username=username,
                        password_hash=hash_password(password),
                        display_name=display,
                        role=role,
                        active=True,
                        created_at=datetime.now(timezone.utc),
                    )
                )
            await session.commit()
            logger.info("Seeded default users: admin / case_manager / compliance")
        elif config.FORCE_RESET_SEED_PASSWORDS:
            for username, password, display, role in seeds:
                user = by_username.get(username) or by_username.get(
                    "analyst" if username == "case_manager" else username
                )
                if user:
                    user.username = username
                    user.password_hash = hash_password(password)
                    user.active = True
                    user.role = role
                    if not user.display_name:
                        user.display_name = display
                else:
                    session.add(
                        User(
                            username=username,
                            password_hash=hash_password(password),
                            display_name=display,
                            role=role,
                            active=True,
                            created_at=datetime.now(timezone.utc),
                        )
                    )
            await session.commit()
            logger.warning(
                "FORCE_RESET_SEED_PASSWORDS=1 — Seed-Passwörter aus .env neu gesetzt. "
                "Danach in .env auf 0 setzen."
            )

    await _migrate_legacy_roles()
    await _ensure_alessio_admin()


async def _migrate_legacy_roles() -> None:
    """Rename analyst → case_manager in place."""
    async with async_session() as session:
        rows = list((await session.execute(select(User))).scalars().all())
        changed = 0
        for user in rows:
            if user.role == "analyst":
                user.role = "case_manager"
                changed += 1
            if user.username == "analyst":
                # Keep login name if already taken as case_manager
                existing = (
                    await session.execute(select(User).where(User.username == "case_manager"))
                ).scalar_one_or_none()
                if not existing:
                    user.username = "case_manager"
                    if user.display_name in {"Analyst", "analyst", ""}:
                        user.display_name = "Case Manager"
                    changed += 1
        if changed:
            await session.commit()
            logger.info("Migrated %s user role/username entries analyst → case_manager", changed)


async def _ensure_alessio_admin() -> None:
    """Ensure personal admin account 'alessio' exists when password is configured."""
    from app.auth import hash_password

    password = (config.SEED_ALESSIO_PASSWORD or "").strip()
    if not password:
        return
    if len(password) < 12:
        logger.warning("SEED_ALESSIO_PASSWORD zu kurz — Alessio-Konto nicht angelegt")
        return

    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.username == "alessio"))
        ).scalar_one_or_none()
        if user:
            user.role = "admin"
            user.display_name = user.display_name or "Alessio"
            user.active = True
            if config.FORCE_RESET_SEED_PASSWORDS:
                user.password_hash = hash_password(password)
            await session.commit()
            return
        session.add(
            User(
                username="alessio",
                password_hash=hash_password(password),
                display_name="Alessio",
                role="admin",
                active=True,
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        logger.info("Created admin user 'alessio'")


def _drop_legacy_bank_tables(conn) -> None:
    """Drop obsolete tables from earlier bank-import eras."""
    insp = inspect(conn)
    tables = set(insp.get_table_names())
    for legacy in ("compliance_hints", "bank_relationships"):
        if legacy in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {legacy}"))
            logger.info("Dropped legacy table %s", legacy)

def _migrate_scan_history_columns(conn) -> None:
    """Add transaction columns to existing SQLite DBs (create_all does not alter tables)."""
    insp = inspect(conn)
    if "scan_history" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("scan_history")}
    additions = {
        "transaction_amount": "REAL",
        "transaction_currency": "VARCHAR(3)",
        "transaction_purpose": "VARCHAR(512)",
    }
    for column, sql_type in additions.items():
        if column not in existing:
            conn.execute(text(f"ALTER TABLE scan_history ADD COLUMN {column} {sql_type}"))


def _migrate_watched_person_columns(conn) -> None:
    insp = inspect(conn)
    if "watched_persons" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("watched_persons")}
    if "case_notes" not in existing:
        conn.execute(text("ALTER TABLE watched_persons ADD COLUMN case_notes VARCHAR(4000)"))
    if "flag_undesired_customer" not in existing:
        conn.execute(
            text(
                "ALTER TABLE watched_persons ADD COLUMN flag_undesired_customer BOOLEAN DEFAULT 0"
            )
        )
    if "flag_aml" not in existing:
        conn.execute(
            text("ALTER TABLE watched_persons ADD COLUMN flag_aml BOOLEAN DEFAULT 0")
        )
    if "scan_priority" not in existing:
        conn.execute(
            text(
                "ALTER TABLE watched_persons ADD COLUMN scan_priority VARCHAR(16) DEFAULT 'normal'"
            )
        )
        # Backfill: case / In-Abklärung / takeover officers are high scan priority
        conn.execute(
            text(
                "UPDATE watched_persons SET scan_priority = 'high' "
                "WHERE source_reason IN "
                "('fraud_list_officer', 'under_investigation', "
                "'shell_takeover_pattern', 'case_open')"
            )
        )
        logger.info("Added watched_persons.scan_priority (+ backfill high sources)")
    try:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_watched_persons_scan_priority "
                "ON watched_persons (scan_priority)"
            )
        )
    except Exception:
        pass


def _migrate_company_case_columns(conn) -> None:
    insp = inspect(conn)
    if "company_cases" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("company_cases")}
    additions = {
        "hit_amount": "REAL",
        "hit_currency": "VARCHAR(3)",
        "hit_reference": "VARCHAR(256)",
        "hit_note": "VARCHAR(1024)",
    }
    for column, sql_type in additions.items():
        if column not in existing:
            conn.execute(text(f"ALTER TABLE company_cases ADD COLUMN {column} {sql_type}"))
            logger.info("Added company_cases.%s", column)


def _migrate_user_2fa_columns(conn) -> None:
    insp = inspect(conn)
    if "users" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("users")}
    additions = {
        "totp_secret_encrypted": "VARCHAR(512)",
        "totp_enabled": "BOOLEAN DEFAULT 0",
        "totp_confirmed_at": "DATETIME",
        "backup_codes_hash": "VARCHAR(4096)",
        "backup_codes_generated_at": "DATETIME",
    }
    for column, sql_type in additions.items():
        if column not in existing:
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {column} {sql_type}"))
            logger.info("Added users.%s", column)


def _scan_to_dict(scan: ScanHistory, checks: list[CheckDetail]) -> dict[str, Any]:
    return {
        "id": scan.id,
        "domain": scan.domain,
        "url": scan.url,
        "company_name": scan.company_name,
        "transaction_amount": scan.transaction_amount,
        "transaction_currency": scan.transaction_currency,
        "transaction_purpose": scan.transaction_purpose,
        "total_score": scan.total_score,
        "verdict": scan.verdict,
        "critical_flags": scan.critical_flags or [],
        "checked_at": scan.checked_at.isoformat() if scan.checked_at else None,
        "checked_by": scan.checked_by,
        "checks": [
            {
                "check_name": c.check_name,
                "status": c.status,
                "score": c.score,
                "max_score": c.max_score,
                "summary": c.summary,
                "details": c.details_json or {},
            }
            for c in checks
        ],
    }


async def save_scan_result(
    report: FullReport,
    company: str | None = None,
    checked_by: str = "unknown",
    transaction_amount: float | None = None,
    transaction_currency: str | None = None,
    transaction_purpose: str | None = None,
) -> int:
    async with async_session() as session:
        scan = ScanHistory(
            domain=report.domain,
            url=report.url,
            company_name=company,
            transaction_amount=transaction_amount,
            transaction_currency=transaction_currency,
            transaction_purpose=transaction_purpose,
            total_score=report.total_score,
            verdict=report.verdict,
            critical_flags=report.critical_flags,
            checked_at=datetime.now(timezone.utc),
            checked_by=checked_by or "unknown",
        )
        session.add(scan)
        await session.flush()
        scan_id = scan.id

        for check in report.checks:
            session.add(
                CheckDetail(
                    scan_id=scan.id,
                    check_name=check.name,
                    status=check.status.value if hasattr(check.status, "value") else str(check.status),
                    score=check.score,
                    max_score=check.max_score,
                    summary=check.summary,
                    details_json=check.details,
                )
            )

        await session.commit()
        return scan_id


async def save_analyst_feedback(
    domain: str,
    url: str,
    feedback_text: str,
    action: str = "dismiss_category",
    original_fraud_category: str | None = None,
    analyst_id: str = "unknown",
) -> None:
    async with async_session() as session:
        session.add(
            AnalystFeedback(
                domain=domain,
                url=url,
                feedback_text=feedback_text,
                action=action,
                original_fraud_category=original_fraud_category,
                analyst_id=analyst_id or "unknown",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def get_scan_by_id(scan_id: int) -> Optional[dict[str, Any]]:
    async with async_session() as session:
        result = await session.execute(
            select(ScanHistory).where(ScanHistory.id == scan_id)
        )
        scan = result.scalar_one_or_none()
        if not scan:
            return None
        checks_result = await session.execute(
            select(CheckDetail).where(CheckDetail.scan_id == scan.id)
        )
        checks = list(checks_result.scalars().all())
        return _scan_to_dict(scan, checks)


async def get_last_scan(domain: str) -> Optional[dict[str, Any]]:
    async with async_session() as session:
        result = await session.execute(
            select(ScanHistory)
            .where(ScanHistory.domain == domain)
            .order_by(ScanHistory.checked_at.desc())
            .limit(1)
        )
        scan = result.scalar_one_or_none()
        if not scan:
            return None

        checks_result = await session.execute(
            select(CheckDetail).where(CheckDetail.scan_id == scan.id)
        )
        checks = list(checks_result.scalars().all())
        return _scan_to_dict(scan, checks)


async def get_scan_history(
    limit: int = 50,
    offset: int = 0,
    verdict_filter: Optional[str] = None,
    domain_search: Optional[str] = None,
) -> dict[str, Any]:
    async with async_session() as session:
        base = select(ScanHistory)
        count_base = select(func.count()).select_from(ScanHistory)

        if verdict_filter:
            base = base.where(ScanHistory.verdict == verdict_filter)
            count_base = count_base.where(ScanHistory.verdict == verdict_filter)

        if domain_search:
            pattern = f"%{domain_search.strip().lower()}%"
            base = base.where(func.lower(ScanHistory.domain).like(pattern))
            count_base = count_base.where(func.lower(ScanHistory.domain).like(pattern))

        total_filtered = (await session.execute(count_base)).scalar_one()

        result = await session.execute(
            base.order_by(ScanHistory.checked_at.desc()).limit(limit).offset(offset)
        )
        scans = list(result.scalars().all())

        scan_ids = [s.id for s in scans]
        checks_by_scan: dict[int, list[CheckDetail]] = {sid: [] for sid in scan_ids}
        if scan_ids:
            checks_result = await session.execute(
                select(CheckDetail).where(CheckDetail.scan_id.in_(scan_ids))
            )
            for check in checks_result.scalars().all():
                checks_by_scan[check.scan_id].append(check)

        items = [_scan_to_dict(scan, checks_by_scan.get(scan.id, [])) for scan in scans]

        total_all = (await session.execute(select(func.count()).select_from(ScanHistory))).scalar_one()

        verdict_rows = await session.execute(
            select(ScanHistory.verdict, func.count())
            .group_by(ScanHistory.verdict)
        )
        verdict_distribution = {row[0]: row[1] for row in verdict_rows.all()}

        avg_score = (await session.execute(select(func.avg(ScanHistory.total_score)))).scalar_one()
        avg_score = round(float(avg_score), 1) if avg_score is not None else 0.0

        top_domains_rows = await session.execute(
            select(ScanHistory.domain, func.count())
            .group_by(ScanHistory.domain)
            .order_by(func.count().desc())
            .limit(5)
        )
        top_domains = [
            {"domain": row[0], "count": row[1]} for row in top_domains_rows.all()
        ]

        return {
            "scans": items,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total_filtered": total_filtered,
                "has_more": offset + len(items) < total_filtered,
            },
            "stats": {
                "total_scans": total_all,
                "verdict_distribution": verdict_distribution,
                "average_score": avg_score,
                "top_domains": top_domains,
            },
        }
