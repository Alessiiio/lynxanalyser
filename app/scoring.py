from __future__ import annotations

from typing import Any

from app.models import CheckResult, CheckStatus, FullReport

# Nominal tier weights (redistributed when an entire tier has no scored checks).
TIER_WEIGHTS: dict[int, float] = {1: 0.55, 2: 0.30, 3: 0.15}

_EXCLUDED_STATUSES = frozenset({
    CheckStatus.SKIPPED,
    CheckStatus.NA,
    CheckStatus.ERROR,
})

_CONSISTENCY_BOOST_SCORE = 5
_CONSISTENCY_YOUNG_DOMAIN_DAYS = 180
_CONTACT_TRUST_THRESHOLD = 8


def enrich_check_tiers(checks: list[CheckResult], tier_by_name: dict[str, int]) -> list[CheckResult]:
    """Attach tier from the check registry when results were created without it."""
    return [
        c.model_copy(update={"tier": tier_by_name.get(c.name, c.tier)})
        for c in checks
    ]


def _is_scored(check: CheckResult) -> bool:
    return check.status not in _EXCLUDED_STATUSES


def _effective_points(check: CheckResult) -> tuple[int, int]:
    """
    Return (earned, max) for tier scoring.
    LLM medium confidence: halve earned points (not max) so uncertain AI signals
    pull toward neutral rather than inflating the score via a smaller denominator.
    """
    if not _is_scored(check):
        return 0, 0

    earned = check.score
    max_pts = check.max_score

    if check.name == "llm_content" and not check.details.get("user_dismissed"):
        if check.details.get("confidence") == "medium":
            earned = earned // 2

    return earned, max_pts


def _find_check(checks: list[CheckResult], name: str) -> CheckResult | None:
    return next((c for c in checks if c.name == name), None)


def apply_domain_age_consistency_modifier(checks: list[CheckResult]) -> list[CheckResult]:
    """
    Post-process WHOIS score for young domains supported by independent trust signals.

    Lives in scoring.py (not whois_check.py) because each check runs in isolation and
    only after all results are available can we correlate WHOIS age with Zefix/FINMA/etc.
    """
    whois = _find_check(checks, "whois")
    if whois is None or not _is_scored(whois):
        return checks

    age_days = whois.details.get("age_days", 9999)
    if age_days >= _CONSISTENCY_YOUNG_DOMAIN_DAYS or whois.score > 2:
        return checks

    trust_details: dict[str, Any] = {}
    available = 0
    positive = 0

    zefix = _find_check(checks, "zefix")
    if zefix is not None and zefix.status not in (CheckStatus.SKIPPED, CheckStatus.NA):
        available += 1
        ok = zefix.status == CheckStatus.PASSED
        positive += int(ok)
        trust_details["zefix"] = {"available": True, "positive": ok, "status": zefix.status.value}

    finma = _find_check(checks, "finma")
    if finma is not None and finma.status != CheckStatus.SKIPPED:
        available += 1
        ok = finma.status == CheckStatus.PASSED
        positive += int(ok)
        trust_details["finma"] = {"available": True, "positive": ok, "status": finma.status.value}

    contact = _find_check(checks, "contact")
    if contact is not None and _is_scored(contact):
        available += 1
        ok = contact.score >= _CONTACT_TRUST_THRESHOLD
        positive += int(ok)
        trust_details["contact"] = {
            "available": True,
            "positive": ok,
            "score": contact.score,
            "threshold": _CONTACT_TRUST_THRESHOLD,
        }

    safebrowsing = _find_check(checks, "safebrowsing")
    virustotal = _find_check(checks, "virustotal")
    sb_ok = (
        safebrowsing is not None
        and safebrowsing.status != CheckStatus.SKIPPED
    )
    vt_ok = (
        virustotal is not None
        and virustotal.status != CheckStatus.SKIPPED
    )
    if sb_ok and vt_ok:
        available += 1
        both_passed = (
            safebrowsing.status == CheckStatus.PASSED
            and virustotal.status == CheckStatus.PASSED
        )
        positive += int(both_passed)
        trust_details["security_databases"] = {
            "available": True,
            "positive": both_passed,
            "safebrowsing": safebrowsing.status.value,
            "virustotal": virustotal.status.value,
        }
    else:
        trust_details["security_databases"] = {
            "available": False,
            "positive": False,
            "note": "Both Safe Browsing and VirusTotal must run to count as a counterweight",
        }

    counterweights = {
        "available": available,
        "positive": positive,
        "details": trust_details,
    }

    if available >= 3 and positive >= 3:
        age_label = f"{age_days} Tage" if age_days < 365 else f"{age_days / 365.25:.1f} Jahre"
        new_whois = whois.model_copy(
            update={
                "score": _CONSISTENCY_BOOST_SCORE,
                "status": CheckStatus.WARNING,
                "summary": (
                    f"Junge Domain ({age_label}), aber durch unabhängige Signale gestützt "
                    f"(Zefix/FINMA/Kontakt/Sicherheitsdatenbanken) — Score angepasst"
                ),
                "details": {
                    **whois.details,
                    "consistency_modifier_applied": True,
                    "trust_counterweights": counterweights,
                    "original_score": whois.score,
                },
            }
        )
        return [new_whois if c.name == "whois" else c for c in checks]

    if "consistency_modifier_applied" not in whois.details:
        new_whois = whois.model_copy(
            update={
                "details": {
                    **whois.details,
                    "consistency_modifier_applied": False,
                    "trust_counterweights": counterweights,
                },
            }
        )
        return [new_whois if c.name == "whois" else c for c in checks]

    return checks


def _compute_tier_subscores(checks: list[CheckResult]) -> dict[int, dict[str, Any]]:
    """Per-tier subscore (0–100) and metadata; tiers with no scored checks are omitted."""
    by_tier: dict[int, list[CheckResult]] = {1: [], 2: [], 3: []}
    for check in checks:
        if _is_scored(check):
            tier = check.tier if check.tier in (1, 2, 3) else 2
            by_tier[tier].append(check)

    result: dict[int, dict[str, Any]] = {}
    for tier, tier_checks in by_tier.items():
        if not tier_checks:
            continue
        earned = 0
        max_pts = 0
        for check in tier_checks:
            e, m = _effective_points(check)
            earned += e
            max_pts += m
        subscore = int((earned / max_pts) * 100) if max_pts > 0 else 0
        result[tier] = {
            "subscore": subscore,
            "checks_evaluated": len(tier_checks),
            "points_earned": earned,
            "points_possible": max_pts,
        }
    return result


def _redistribute_weights(active_tiers: list[int]) -> dict[int, float]:
    """Proportionally scale nominal weights onto tiers that contributed scored checks."""
    raw = {t: TIER_WEIGHTS[t] for t in active_tiers}
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {t: w / total for t, w in raw.items()}


def _whois_age_days(checks: list[CheckResult]) -> int | None:
    whois = _find_check(checks, "whois")
    if whois is None or not _is_scored(whois):
        return None
    age = whois.details.get("age_days")
    return int(age) if age is not None else None


def _build_warning_flags(checks: list[CheckResult]) -> list[str]:
    """Soft signals — inform analysts without overriding the verdict to Critical Risk."""
    warnings: list[str] = []

    contact = _find_check(checks, "contact")
    if contact is None or not contact.details.get("email_domain_mismatch"):
        return warnings

    email = contact.details.get("extracted_email", "")
    severity = contact.details.get("email_mismatch_severity")
    age_days = _whois_age_days(checks)

    if severity == "high":
        # Free webmail on an old established domain → warning only, not critical.
        if age_days is not None and age_days >= 365:
            warnings.append(
                "Kontakt-E-Mail über Free-Webmail-Dienst — bei etablierter Domain "
                "häufig harmlos, kurz gegen Impressum prüfen"
            )
        # Young domain + free webmail handled as critical in _build_critical_flags.
        return warnings

    warnings.append(
        "Kontakt-E-Mail nutzt eine andere Domain als die Webseite "
        f"({email}) — bei Konzernen und Dienstleistern üblich, kein alleiniger Betrugsindikator"
    )
    return warnings


def _llm_answer_evidence_valid(answers: list, question: int) -> bool:
    if not isinstance(answers, list):
        return False
    for entry in answers:
        if not isinstance(entry, dict):
            continue
        if entry.get("question") == question and entry.get("answer") == "yes":
            return entry.get("evidence_valid") is True
    return False


def _build_critical_flags(checks: list[CheckResult]) -> list[str]:
    critical_flags: list[str] = []

    for check in checks:
        if check.name == "safebrowsing" and check.status == CheckStatus.FAILED:
            threat_types = check.details.get("threat_types", [])
            critical_flags.append(
                "Flagged by Google Safe Browsing"
                + (f": {', '.join(threat_types)}" if threat_types else "")
            )
        if check.name == "virustotal" and check.details.get("malicious", 0) > 5:
            critical_flags.append(
                f"VirusTotal: {check.details['malicious']} security engines flagged this domain"
            )
        if check.name == "whois" and 0 < check.details.get("age_days", 9999) < 30:
            critical_flags.append("Domain registered less than 30 days ago")
        if check.name == "llm_content":
            if check.details.get("user_dismissed"):
                continue
            confidence = check.details.get("confidence")
            if confidence != "high":
                continue
            answers = check.details.get("answers")
            if not isinstance(answers, list):
                continue
            if _llm_answer_evidence_valid(answers, 3):
                critical_flags.append(
                    "AI-Analyse: Identitätsmissbrauch/Phishing mit hoher Sicherheit erkannt"
                )
            elif _llm_answer_evidence_valid(answers, 12):
                critical_flags.append(
                    "AI-Analyse: Support-Scam mit Aufforderung zu Fernzugriff/Anruf erkannt — "
                    "hohes Risiko für direkten Gerätezugriff"
                )
            elif (
                _llm_answer_evidence_valid(answers, 1)
                and _llm_answer_evidence_valid(answers, 7)
            ):
                critical_flags.append(
                    "AI-Analyse: Anlagebetrug-Muster (unrealistische Rendite + "
                    "verdächtige Zahlungswege) mit hoher Sicherheit erkannt"
                )
            elif _llm_answer_evidence_valid(answers, 13):
                tx_ctx = check.details.get("transaction_context") or {}
                amount = tx_ctx.get("amount")
                currency = tx_ctx.get("currency", "CHF")
                if amount is not None:
                    from app.transaction_context import to_chf_equivalent
                    chf_equiv = to_chf_equivalent(float(amount), str(currency))
                    if chf_equiv > 5000:
                        critical_flags.append(
                            f"Transaktionskontext-Widerspruch: Betrag {amount:g} {currency} "
                            f"passt nicht zum erkennbaren Angebot der Webseite"
                        )
        if check.name == "finma" and check.details.get("listed"):
            critical_flags.append(
                f"FINMA warning list: {check.details.get('warning_name', 'match')}"
            )
        if check.name == "iscan" and check.details.get("listed"):
            regulator = check.details.get("regulator") or "IOSCO member regulator"
            critical_flags.append(
                f"I-SCAN warning list ({regulator}): {check.details.get('warning_name', 'match')}"
            )
        if check.name == "contact" and check.details.get("email_domain_mismatch"):
            if check.details.get("email_mismatch_severity") != "high":
                continue
            age_days = _whois_age_days(checks)
            # Free webmail on a young domain is a strong phishing pattern.
            if age_days is not None and age_days < 365:
                email = check.details.get("extracted_email", "")
                critical_flags.append(
                    f"Kontakt-E-Mail über Free-Webmail bei junger Domain ({email})"
                )
        if check.name == "zefix":
            fnm = check.details.get("fraud_network_match") or {}
            if fnm.get("matched"):
                hit = (fnm.get("hits") or [{}])[0]
                src = hit.get("source_company_name") or "Fraud-Watchlist"
                name = hit.get("display_name") or "bekannte Person"
                critical_flags.append(
                    f"Fraud-Watchlist: {name} (bekannt aus: {src})"
                )
            shell = check.details.get("shell_takeover_pattern") or {}
            if shell.get("pattern_detected") and shell.get("confidence") == "high":
                critical_flags.append(
                    "Shell-Takeover-Muster: altes Unternehmen mit plötzlichem Organwechsel"
                )

    return critical_flags


def calculate_score(checks: list[CheckResult], domain: str, url: str) -> FullReport:
    checks = apply_domain_age_consistency_modifier(checks)

    tier_data = _compute_tier_subscores(checks)
    active_tiers = sorted(tier_data.keys())
    weights = _redistribute_weights(active_tiers)

    if weights:
        normalized = int(
            sum(tier_data[t]["subscore"] * weights[t] for t in active_tiers)
        )
    else:
        normalized = 0

    tier_breakdown: dict[str, Any] = {}
    for tier in (1, 2, 3):
        if tier in tier_data:
            tier_breakdown[str(tier)] = {
                "subscore": tier_data[tier]["subscore"],
                "weight": round(weights.get(tier, 0.0), 4),
                "weight_percent": round(weights.get(tier, 0.0) * 100, 1),
                "checks_evaluated": tier_data[tier]["checks_evaluated"],
                "points_earned": tier_data[tier]["points_earned"],
                "points_possible": tier_data[tier]["points_possible"],
                "included": True,
            }
        else:
            tier_breakdown[str(tier)] = {
                "subscore": None,
                "weight": 0.0,
                "weight_percent": 0.0,
                "checks_evaluated": 0,
                "included": False,
                "reason": "no_scored_checks",
            }

    critical_flags = _build_critical_flags(checks)
    warning_flags = _build_warning_flags(checks)

    if critical_flags:
        verdict = "Critical Risk"
        verdict_color = "red"
    elif normalized >= 75:
        verdict = "Likely Legitimate"
        verdict_color = "green"
    elif normalized >= 50:
        verdict = "Use Caution"
        verdict_color = "yellow"
    elif normalized >= 25:
        verdict = "High Risk"
        verdict_color = "orange"
    else:
        verdict = "Likely Fraudulent"
        verdict_color = "red"

    return FullReport(
        url=url,
        domain=domain,
        total_score=normalized,
        max_possible=100,
        verdict=verdict,
        verdict_color=verdict_color,
        critical_flags=critical_flags,
        warning_flags=warning_flags,
        checks=checks,
        tier_breakdown=tier_breakdown,
    )
