from __future__ import annotations

from pydantic import BaseModel
from typing import Optional, Any
from enum import Enum


class CheckStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    NA = "na"
    LOADING = "loading"


class CheckResult(BaseModel):
    name: str
    display_name: str
    status: CheckStatus
    score: int
    max_score: int
    summary: str
    details: dict[str, Any] = {}
    tier: int = 2


class CheckRequest(BaseModel):
    url: str
    company: Optional[str] = None
    transaction_amount: Optional[float] = None
    transaction_currency: Optional[str] = None
    transaction_purpose: Optional[str] = None


class PreviousScanInfo(BaseModel):
    previous_score: int
    previous_verdict: str
    previous_checked_at: str
    score_diff: int


class FullReport(BaseModel):
    url: str
    domain: str
    total_score: int
    max_possible: int
    verdict: str
    verdict_color: str
    critical_flags: list[str]
    warning_flags: list[str] = []
    checks: list[CheckResult]
    tier_breakdown: dict[str, Any] = {}
    previous_scan: Optional[PreviousScanInfo] = None
    goldlist_match: Optional[bool] = None
    blocklist_match: Optional[bool] = None
    cached: bool = False
    scan_id: Optional[int] = None


class LlmFeedbackRequest(BaseModel):
    url: str
    domain: str
    feedback_text: str = ""
    checks: list[CheckResult]
    previous_scan: Optional[PreviousScanInfo] = None
    analyst_id: str = "unknown"


class GoldlistUpdateRequest(BaseModel):
    domain: str


class FraudConfirmRequest(BaseModel):
    domain: str
    url: str
    fraud_category: str = "general_suspicious"
    feedback_text: str = ""
    checks: list[CheckResult] = []
    analyst_id: str = "unknown"
