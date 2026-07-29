from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlparse

import httpx

from app.checks.base import BaseCheck
from app.checks.whois_check import WhoisCheck
from app.checks.ssl_check import SSLCheck
from app.checks.dns_check import DNSCheck
from app.checks.hosting_check import HostingCheck
from app.checks.contact_check import ContactCheck
from app.checks.wayback_check import WaybackCheck
from app.checks.crt_check import CRTCheck
from app.checks.hsts_check import HSTSCheck
from app.checks.zefix_check import ZefixCheck
from app.checks.virustotal_check import VirusTotalCheck
from app.checks.safebrowsing_check import SafeBrowsingCheck
from app.checks.urlscan_check import URLScanCheck
from app.checks.llm_content_check import LLMContentCheck
from app.checks.finma_check import FINMACheck
from app.checks.iscan_check import ISCANCheck
from app.checks.trustpilot_check import TrustpilotCheck
from app.checks.google_reviews_check import GoogleReviewsCheck
from app.checks.social_media_check import SocialMediaCheck
from app.models import CheckResult, CheckStatus, FullReport, PreviousScanInfo
from app.database import get_last_scan, save_scan_result
from app.scoring import calculate_score, enrich_check_tiers
from app.cache import get_cached_report, set_cached_report
from app.goldlist import goldlist_info, is_goldlisted
from app.blocklist import blocklist_entry, blocklist_info, is_blocklisted
from app.fraud_confirm import build_blocklist_report
from app.checks.llm_context import build_llm_check_context
from app.transaction_context import TransactionContext

logger = logging.getLogger(__name__)

ALL_CHECKS: list[BaseCheck] = [
    WhoisCheck(),
    SSLCheck(),
    DNSCheck(),
    HostingCheck(),
    WaybackCheck(),
    CRTCheck(),
    HSTSCheck(),
    ContactCheck(),
    TrustpilotCheck(),
    GoogleReviewsCheck(),
    SocialMediaCheck(),
    VirusTotalCheck(),
    SafeBrowsingCheck(),
    URLScanCheck(),
    ZefixCheck(),
    FINMACheck(),
    ISCANCheck(),
    LLMContentCheck(),
]

_CHECK_TIMEOUTS: dict[str, float] = {
    "urlscan": 30.0,
    "crt": 15.0,
    "whois": 15.0,
    "wayback": 20.0,
    "llm_content": 90.0,
    "ssl": 20.0,
    "contact": 35.0,
    "trustpilot": 120.0,
    "google_reviews": 25.0,
    "social_media": 45.0,
    "finma": 35.0,
    "iscan": 25.0,
}
_DEFAULT_TIMEOUT = 15.0
_MAX_RETRIES = 2
_LLM_MAX_RETRIES = 2  # connection issues only; JSON/parse errors are not retried
_RETRY_DELAY = 1.0

# For goldlisted domains: skip slow checks, only run critical safety signals
_GOLDLIST_SAFETY_CHECKS = frozenset({"safebrowsing", "virustotal", "finma", "iscan"})

_CHECK_THOUGHT_START: dict[str, str] = {
    "whois": "WHOIS-Abfrage: Registrierungsdatum und Registrar prüfen…",
    "ssl": "TLS-Verbindung aufbauen, Zertifikat und Aussteller validieren…",
    "dns": "DNS-Einträge laden (MX, SPF, DMARC)…",
    "hosting": "Server-Standort und Hosting-Provider ermitteln…",
    "wayback": "Web-Archiv durchsuchen — erste bekannte Snapshot-Daten…",
    "crt": "Zertifikatshistorie bei crt.sh abfragen (kann dauern)…",
    "hsts": "HSTS-Konfiguration und Preload-Status prüfen…",
    "contact": "Startseite laden — Kontaktangaben und Impressum suchen…",
    "trustpilot": "Trustpilot: Bewertungsprofil und Review-Muster analysieren…",
    "google_reviews": "Google Maps: Unternehmensbewertungen und Review-Muster prüfen…",
    "social_media": "Social-Media-Links auf der Seite finden und Profile bewerten…",
    "virustotal": "VirusTotal: Domain-Reputation bei Security-Engines abgleichen…",
    "safebrowsing": "Google Safe Browsing: Malware- und Phishing-Listen prüfen…",
    "urlscan": "URLScan.io: Seite scannen und Verhalten analysieren (kann dauern)…",
    "zefix": "Zefix-Handelsregister: Schweizer Firmeneintrag suchen…",
    "finma": "FINMA-Warnliste: Schweizer Regulierungs-Warnungen abgleichen…",
    "iscan": "I-SCAN (IOSCO): Internationale Regulierungs-Warnliste abgleichen…",
    "llm_content": "KI-Analyse: Mehrseiten-Inhalt sammeln, Fakten extrahieren, 10 Fragen beantworten…",
}


def _check_thought_start(name: str) -> str:
    return _CHECK_THOUGHT_START.get(name, f"Prüfung «{name}» wird ausgeführt…")


def _max_retries_for_check(check_name: str) -> int:
    return _LLM_MAX_RETRIES if check_name == "llm_content" else _MAX_RETRIES


def _should_retry_check_result(result: CheckResult) -> bool:
    if result.status != CheckStatus.ERROR:
        return False
    if result.details.get("retryable") is False:
        return False
    if result.name == "llm_content":
        summary = result.summary.lower()
        return any(
            phrase in summary
            for phrase in ("nicht erreichbar", "connection", "http 5", "timeout")
        )
    return True


def extract_domain(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    netloc = urlparse(url).netloc or urlparse(url).path
    domain = netloc.split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.lower().strip("/")


def _looks_like_tagline(text: str) -> bool:
    lower = text.lower()
    if len(text.split()) > 5:
        return True
    tagline_markers = (
        "invest in", "welcome to", "your ", " discover ", "learn more",
        "get started", "we help", "leading provider", "official site",
    )
    return any(marker in lower for marker in tagline_markers)


def _brand_from_domain(domain: str) -> str:
    label = domain.lower().removeprefix("www.").split(".")[0]
    return label.replace("-", " ").strip()


def _pick_company_candidate(candidate: str, domain: str) -> str | None:
    candidate = candidate.strip()
    if len(candidate) < 3 or len(candidate) > 80:
        return None
    if _looks_like_tagline(candidate):
        return None
    brand = _brand_from_domain(domain)
    if brand and len(brand) >= 3:
        cand_norm = candidate.lower().replace(" ", "").replace("-", "")
        brand_norm = brand.replace(" ", "").replace("-", "")
        if brand_norm in cand_norm or cand_norm in brand_norm:
            return candidate
    if len(candidate.split()) <= 4 and not _looks_like_tagline(candidate):
        return candidate
    return None


async def _detect_company_from_ssl_and_web(domain: str, ssl_result: CheckResult | None) -> str | None:
    if ssl_result and ssl_result.details.get("organization"):
        org = ssl_result.details["organization"].strip()
        picked = _pick_company_candidate(org, domain)
        if picked:
            return picked

    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.get(f"https://{domain}")
            html = resp.text

        for pattern in [
            r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\'](.*?)["\']',
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:site_name["\']',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                picked = _pick_company_candidate(m.group(1).strip(), domain)
                if picked:
                    return picked

        m = re.search(r"<title>([^<]{3,120})</title>", html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
            parts = [p.strip() for p in re.split(r"\s*[|–—\-]\s*", title) if p.strip()]
            brand = _brand_from_domain(domain)
            brand_norm = brand.replace(" ", "").replace("-", "").lower()

            for part in reversed(parts):
                part_norm = part.lower().replace(" ", "").replace("-", "")
                if brand_norm and (brand_norm in part_norm or part_norm == brand_norm):
                    picked = _pick_company_candidate(part, domain)
                    if picked:
                        return picked

            for part in parts:
                picked = _pick_company_candidate(part, domain)
                if picked:
                    return picked
    except Exception:
        pass

    return None


async def _wrap_with_timeout(check: BaseCheck, timeout: float, **kwargs) -> CheckResult:
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(check.run(**kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        result = CheckResult(
            name=check.name,
            display_name=check.display_name,
            status=CheckStatus.ERROR,
            score=0,
            max_score=check.max_score,
            summary=f"Timed out after {int(timeout)}s",
            details={},
        )
    except Exception as e:
        result = CheckResult(
            name=check.name,
            display_name=check.display_name,
            status=CheckStatus.ERROR,
            score=0,
            max_score=check.max_score,
            summary=f"Unexpected error: {str(e)[:120]}",
            details={},
        )
    elapsed = time.monotonic() - start
    print(f"[TIMING] {check.name}: {elapsed:.1f}s (status: {result.status.value})")
    logger.info("[TIMING] %s: %.1fs (status: %s)", check.name, elapsed, result.status.value)
    return result


async def _attach_previous_scan(report: FullReport, previous_task: asyncio.Task) -> FullReport:
    try:
        previous = await previous_task
    except Exception as e:
        logger.warning("Failed to load previous scan for %s: %s", report.domain, e)
        return report

    if not previous:
        return report

    return report.model_copy(
        update={
            "previous_scan": PreviousScanInfo(
                previous_score=previous["total_score"],
                previous_verdict=previous["verdict"],
                previous_checked_at=previous["checked_at"],
                score_diff=report.total_score - previous["total_score"],
            )
        }
    )


def _tier_by_name() -> dict[str, int]:
    return {c.name: c.tier for c in ALL_CHECKS}


def _build_report(completed: list[CheckResult], domain: str, url: str) -> FullReport:
    completed = enrich_check_tiers(completed, _tier_by_name())
    return calculate_score(completed, domain=domain, url=url)


def _annotate_report(report: FullReport) -> FullReport:
    gold = goldlist_info(report.domain)
    block = blocklist_info(report.domain)
    return report.model_copy(
        update={
            "goldlist_match": bool(gold and gold.get("listed")),
            "blocklist_match": bool(block and block.get("listed")),
        }
    )


def _blocklist_skip_result(check: BaseCheck) -> CheckResult:
    return CheckResult(
        name=check.name,
        display_name=check.display_name,
        status=CheckStatus.SKIPPED,
        score=0,
        max_score=check.max_score,
        summary="Übersprungen — Domain auf interner Blocklist (bestätigter Betrug)",
        details={"blocklist_skip": True},
    )


async def _run_blocklist_fast_path(
    domain: str,
    url: str,
    company: str | None,
    previous_task: asyncio.Task,
    transaction: TransactionContext | None = None,
) -> FullReport:
    entry = blocklist_entry(domain) or {"fraud_category": "general_suspicious", "note": ""}
    completed = [_blocklist_skip_result(check) for check in ALL_CHECKS]
    report = build_blocklist_report(
        enrich_check_tiers(completed, _tier_by_name()),
        domain=domain,
        url=url,
        entry=entry,
    )
    report = await _attach_previous_scan(report, previous_task)
    set_cached_report(domain, company, report, transaction)
    _schedule_save(report, company, transaction)
    return report


def _goldlist_skip_result(check: BaseCheck) -> CheckResult:
    return CheckResult(
        name=check.name,
        display_name=check.display_name,
        status=CheckStatus.SKIPPED,
        score=0,
        max_score=check.max_score,
        summary="Übersprungen — Domain auf interner Goldlist",
        details={"goldlist_skip": True},
    )


async def _run_goldlist_safety_checks(
    domain: str, url: str, company: str | None
) -> list[CheckResult]:
    kwargs = {"domain": domain, "url": url, "company_name": company}
    safety = [c for c in ALL_CHECKS if c.name in _GOLDLIST_SAFETY_CHECKS]
    tasks = [
        _wrap_with_timeout(
            check,
            timeout=_CHECK_TIMEOUTS.get(check.name, _DEFAULT_TIMEOUT),
            **kwargs,
        )
        for check in safety
    ]
    return list(await asyncio.gather(*tasks))


def _schedule_save(
    report: FullReport,
    company: str | None,
    transaction: TransactionContext | None = None,
) -> None:
    async def _save() -> None:
        try:
            await save_scan_result(
                report,
                company=company,
                transaction_amount=transaction.amount if transaction else None,
                transaction_currency=transaction.currency if transaction else None,
                transaction_purpose=transaction.purpose if transaction else None,
            )
        except Exception as e:
            logger.error("Failed to save scan result for %s: %s", report.domain, e)

    asyncio.create_task(_save())


async def run_all_checks(
    url: str,
    company: str | None = None,
    transaction: TransactionContext | None = None,
) -> FullReport:
    domain = extract_domain(url)
    cached = get_cached_report(domain, company, transaction)
    if cached is not None:
        return cached.model_copy(update={"cached": True})

    previous_task = asyncio.create_task(get_last_scan(domain))

    if is_blocklisted(domain):
        return await _run_blocklist_fast_path(domain, url, company, previous_task, transaction)

    if is_goldlisted(domain):
        completed: list[CheckResult] = []
        for check in ALL_CHECKS:
            if check.name not in _GOLDLIST_SAFETY_CHECKS:
                completed.append(_goldlist_skip_result(check))
        completed.extend(await _run_goldlist_safety_checks(domain, url, company))
        report = _annotate_report(_build_report(completed, domain=domain, url=url))
        report = await _attach_previous_scan(report, previous_task)
        set_cached_report(domain, company, report, transaction)
        _schedule_save(report, company, transaction)
        return report

    kwargs = {"domain": domain, "url": url, "company_name": company}

    llm_check = next((c for c in ALL_CHECKS if c.name == "llm_content"), None)
    parallel_checks = [c for c in ALL_CHECKS if c.name != "llm_content"]

    tasks = [
        _wrap_with_timeout(check, timeout=_CHECK_TIMEOUTS.get(check.name, _DEFAULT_TIMEOUT), **kwargs)
        for check in parallel_checks
    ]
    results = list(await asyncio.gather(*tasks))

    if llm_check:
        llm_kwargs = {
            **kwargs,
            "check_context": build_llm_check_context(results),
            "transaction_context": transaction,
        }
        llm_result = await _wrap_with_timeout(
            llm_check,
            timeout=_CHECK_TIMEOUTS.get("llm_content", _DEFAULT_TIMEOUT),
            **llm_kwargs,
        )
        results.append(llm_result)
    report = _annotate_report(_build_report(list(results), domain=domain, url=url))
    report = await _attach_previous_scan(report, previous_task)
    set_cached_report(domain, company, report, transaction)
    _schedule_save(report, company, transaction)
    return report


async def stream_checks(
    url: str,
    company: str | None = None,
    transaction: TransactionContext | None = None,
):
    """Async generator yielding retry/check events as checks run, then a FullReport."""
    domain = extract_domain(url)
    cached = get_cached_report(domain, company, transaction)
    if cached is not None:
        yield ("report", cached.model_copy(update={"cached": True}))
        return

    previous_task = asyncio.create_task(get_last_scan(domain))

    if is_blocklisted(domain):
        entry = blocklist_entry(domain) or {}
        yield ("blocklist", {"domain": domain, "category": entry.get("fraud_category")})
        yield ("thought", {"text": f"«{domain}» steht auf der internen Blocklist — bekannter Betrug"})
        completed_bl: list[CheckResult] = []
        for check in ALL_CHECKS:
            result = _blocklist_skip_result(check)
            completed_bl.append(result)
            yield ("check", result)
        yield ("thought", {"text": "Blocklist-Treffer — Score auf Kritisches Risiko gesetzt"})
        report = build_blocklist_report(
            enrich_check_tiers(completed_bl, _tier_by_name()),
            domain=domain,
            url=url,
            entry=entry or {"fraud_category": "general_suspicious", "note": ""},
        )
        report = await _attach_previous_scan(report, previous_task)
        set_cached_report(domain, company, report, transaction)
        _schedule_save(report, company, transaction)
        yield ("report", report)
        return

    if is_goldlisted(domain):
        yield ("goldlist", {"domain": domain})
        yield ("thought", {"text": f"«{domain}» steht auf der internen Goldlist — Schnellprüfung"})
        skipped_count = len(ALL_CHECKS) - len(_GOLDLIST_SAFETY_CHECKS)
        yield ("thought", {"text": f"Überspringe {skipped_count} Standard-Prüfungen (bekannte legitime Domain)…"})
        completed: list[CheckResult] = []
        for check in ALL_CHECKS:
            if check.name not in _GOLDLIST_SAFETY_CHECKS:
                result = _goldlist_skip_result(check)
                completed.append(result)
                yield ("check", result)

        yield ("thought", {"text": "Führe Sicherheits-Basics aus (Safe Browsing, VirusTotal, FINMA, I-SCAN)…"})
        for name in sorted(_GOLDLIST_SAFETY_CHECKS):
            yield ("thought", {"text": _check_thought_start(name)})

        safety_results = await _run_goldlist_safety_checks(domain, url, company)
        for result in safety_results:
            completed.append(result)
            yield ("check", result)

        yield ("thought", {"text": "Alle Prüfungen abgeschlossen — berechne Score und Verdict…"})
        report = _annotate_report(_build_report(completed, domain=domain, url=url))
        report = await _attach_previous_scan(report, previous_task)
        set_cached_report(domain, company, report, transaction)
        _schedule_save(report, company, transaction)
        yield ("report", report)
        return

    completed: list[CheckResult] = []

    yield ("thought", {"text": f"Ziel-Domain «{domain}» — starte {len(ALL_CHECKS)} Prüfungen parallel…"})

    kwargs = {"domain": domain, "url": url, "company_name": company}

    llm_check = next((c for c in ALL_CHECKS if c.name == "llm_content"), None)
    parallel_checks = [c for c in ALL_CHECKS if c.name != "llm_content"]

    if parallel_checks:
        yield ("thought", {"text": f"Starte {len(parallel_checks)} parallele Prüfungen…"})

    # Queue receives ("retry", dict), ("thought", dict) and ("check", CheckResult) from concurrent tasks
    event_queue: asyncio.Queue = asyncio.Queue()

    async def _run_with_retries(check: BaseCheck) -> None:
        timeout = _CHECK_TIMEOUTS.get(check.name, _DEFAULT_TIMEOUT)
        max_attempts = _max_retries_for_check(check.name)
        task_start = time.monotonic()
        try:
            await event_queue.put(("thought", {"text": _check_thought_start(check.name)}))
            for attempt in range(1, max_attempts + 1):
                result = await _wrap_with_timeout(check, timeout=timeout, **kwargs)
                if result.status != CheckStatus.ERROR or attempt == max_attempts:
                    await event_queue.put(("check", result))
                    elapsed = time.monotonic() - task_start
                    print(
                        f"[TIMING] {check.name}: {elapsed:.1f}s total "
                        f"(status: {result.status.value}, attempts: {attempt})"
                    )
                    return
                if not _should_retry_check_result(result):
                    await event_queue.put(("check", result))
                    elapsed = time.monotonic() - task_start
                    print(
                        f"[TIMING] {check.name}: {elapsed:.1f}s total "
                        f"(status: {result.status.value}, attempts: {attempt})"
                    )
                    return
                await event_queue.put(("retry", {
                    "name": check.name,
                    "display_name": check.display_name,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                }))
                await event_queue.put(("thought", {
                    "text": (
                        f"{check.display_name}: Verbindung fehlgeschlagen — "
                        f"Versuch {attempt + 1}/{max_attempts}…"
                    ),
                }))
                await asyncio.sleep(_RETRY_DELAY)
        except Exception as e:
            await event_queue.put(("check", CheckResult(
                name=check.name,
                display_name=check.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=check.max_score,
                summary=f"Unexpected error: {str(e)[:120]}",
                details={},
            )))

    tasks = [asyncio.create_task(_run_with_retries(check)) for check in parallel_checks]

    pending_count = len(tasks)
    while pending_count > 0:
        event_type, data = await event_queue.get()
        if event_type == "check":
            completed.append(data)
            pending_count -= 1
            yield ("check", data)
        elif event_type == "thought":
            yield ("thought", data)
        else:
            yield ("retry", data)

    # Auto-detect company from SSL result if not provided (post-parallel, non-blocking for other checks)
    resolved_company = company
    if not company:
        ssl_result = next((r for r in completed if r.name == "ssl"), None)
        detected = await _detect_company_from_ssl_and_web(domain, ssl_result)
        if detected:
            resolved_company = detected
            yield ("thought", {
                "text": f"Firmenname erkannt: «{detected}» — für künftige Zefix/FINMA-Abgleiche",
            })

    saved_company = company or resolved_company
    if llm_check:
        yield ("thought", {"text": _check_thought_start("llm_content")})
        llm_timeout = _CHECK_TIMEOUTS.get("llm_content", _DEFAULT_TIMEOUT)
        llm_kwargs = {
            **kwargs,
            "check_context": build_llm_check_context(completed),
            "transaction_context": transaction,
        }
        llm_result = None
        llm_max_attempts = _max_retries_for_check("llm_content")
        for attempt in range(1, llm_max_attempts + 1):
            llm_result = await _wrap_with_timeout(llm_check, timeout=llm_timeout, **llm_kwargs)
            if llm_result.status != CheckStatus.ERROR or attempt == llm_max_attempts:
                break
            if not _should_retry_check_result(llm_result):
                break
            yield ("retry", {
                "name": llm_check.name,
                "display_name": llm_check.display_name,
                "attempt": attempt,
                "max_attempts": llm_max_attempts,
            })
            yield ("thought", {
                "text": (
                    f"{llm_check.display_name}: Verbindung fehlgeschlagen — "
                    f"Versuch {attempt + 1}/{llm_max_attempts}…"
                ),
            })
            await asyncio.sleep(_RETRY_DELAY)
        completed.append(llm_result)
        yield ("check", llm_result)

    yield ("thought", {"text": "Alle Prüfungen abgeschlossen — berechne Score und Verdict…"})
    report = _annotate_report(_build_report(completed, domain=domain, url=url))
    report = await _attach_previous_scan(report, previous_task)
    set_cached_report(domain, company, report, transaction)
    _schedule_save(report, saved_company, transaction)
    yield ("report", report)
