"""Build cross-check context for LLM analysis from completed check results."""

from __future__ import annotations

from app.models import CheckResult, CheckStatus


def _find_check(checks: list[CheckResult], name: str) -> CheckResult | None:
    return next((c for c in checks if c.name == name), None)


def _short_status(check: CheckResult | None) -> str:
    if check is None:
        return "nicht ausgeführt"
    if check.status == CheckStatus.SKIPPED:
        return "übersprungen"
    if check.status == CheckStatus.NA:
        return "n/a"
    if check.status == CheckStatus.ERROR:
        return "Fehler"
    return f"{check.status.value} ({check.score}/{check.max_score})"


def build_llm_check_context(checks: list[CheckResult]) -> dict:
    """Summarize deterministic checks the LLM should not re-litigate."""
    contact = _find_check(checks, "contact")
    finma = _find_check(checks, "finma")
    iscan = _find_check(checks, "iscan")
    zefix = _find_check(checks, "zefix")
    whois = _find_check(checks, "whois")
    trustpilot = _find_check(checks, "trustpilot")
    google_reviews = _find_check(checks, "google_reviews")

    ctx: dict = {
        "lines": [],
        "contact": None,
        "finma": None,
        "iscan": None,
        "zefix": None,
        "whois": None,
        "trustpilot": None,
        "google_reviews": None,
        "contradiction_hints": [],
    }

    if contact and contact.status not in (CheckStatus.SKIPPED, CheckStatus.ERROR):
        cd = contact.details
        ctx["contact"] = {
            "status": contact.status.value,
            "score": contact.score,
            "max_score": contact.max_score,
            "impressum_found": bool(cd.get("impressum_link_found")),
            "phone": bool(cd.get("extracted_phone")),
            "email": bool(cd.get("extracted_email")),
            "address": bool(cd.get("address_found")),
            "found_on_subpage": bool(cd.get("found_on_subpage")),
        }
        ctx["lines"].append(
            f"Contact-Check: {contact.score}/{contact.max_score}, "
            f"Impressum={'ja' if ctx['contact']['impressum_found'] else 'nein'}, "
            f"Telefon={'ja' if ctx['contact']['phone'] else 'nein'}, "
            f"E-Mail={'ja' if ctx['contact']['email'] else 'nein'}, "
            f"Adresse={'ja' if ctx['contact']['address'] else 'nein'}"
        )

    if finma and finma.status != CheckStatus.SKIPPED:
        listed = bool(finma.details.get("listed"))
        ctx["finma"] = {"listed": listed, "status": finma.status.value}
        ctx["lines"].append(
            "FINMA-Warnliste: EINGETRAGEN — Schweizer Regulierungs-Warnung bestätigt!"
            if listed
            else "FINMA-Warnliste: nicht gelistet"
        )
        if listed:
            ctx["contradiction_hints"].append(
                "FINMA-Warnliste: Domain/Unternehmen offiziell gemeldet"
            )

    if iscan and iscan.status != CheckStatus.SKIPPED:
        listed = bool(iscan.details.get("listed"))
        ctx["iscan"] = {"listed": listed, "status": iscan.status.value}
        ctx["lines"].append(
            "I-SCAN-Warnliste: EINGETRAGEN — internationale Regulierungs-Warnung bestätigt!"
            if listed
            else "I-SCAN-Warnliste (IOSCO): nicht gelistet"
        )
        if listed:
            regulator = iscan.details.get("regulator")
            if regulator:
                ctx["lines"][-1] = f"I-SCAN-Warnliste: EINGETRAGEN ({regulator})"
            ctx["contradiction_hints"].append(
                "I-SCAN: Domain/Unternehmen in internationaler Warnliste gemeldet"
            )

    if zefix and zefix.status not in (CheckStatus.SKIPPED, CheckStatus.NA, CheckStatus.ERROR):
        mutation_warnings = [
            w for w in (zefix.details.get("warning_flags") or [])
            if not str(w).startswith("Neueintragung vom")
        ]
        ctx["zefix"] = {
            "status": zefix.status.value,
            "score": zefix.score,
            "company": zefix.details.get("name") or zefix.details.get("company_name"),
            "mutation_warnings": mutation_warnings,
            "latest_mutation_date": zefix.details.get("latest_mutation_date"),
        }
        ctx["lines"].append(
            f"Zefix: {zefix.summary[:120]}"
        )
        for warning in mutation_warnings[:3]:
            ctx["lines"].append(f"Zefix SHAB: {warning}")
            ctx["contradiction_hints"].append(f"Handelsregister: {warning}")

    if whois and whois.status not in (CheckStatus.SKIPPED, CheckStatus.ERROR):
        age = whois.details.get("age_days")
        ctx["whois"] = {"age_days": age, "status": whois.status.value}
        if age is not None:
            ctx["lines"].append(f"Domain-Alter: {age} Tage")

    if trustpilot and trustpilot.details.get("profile_found"):
        neg = trustpilot.details.get("negative_review_analysis") or {}
        ctx["trustpilot"] = {
            "score": trustpilot.score,
            "trust_score": trustpilot.details.get("trust_score"),
            "total_reviews": trustpilot.details.get("total_reviews"),
            "warnings": trustpilot.details.get("warning_flags", []),
            "negative_review_severity": neg.get("overall_severity"),
            "negative_review_summary": neg.get("summary"),
        }
        ctx["lines"].append(
            f"Trustpilot: {trustpilot.details.get('trust_score')}/5, "
            f"{trustpilot.details.get('total_reviews')} Reviews, Legitimitätsscore {trustpilot.score}/12"
        )
        if neg and not neg.get("skipped") and neg.get("overall_severity") in ("low", "high"):
            ctx["lines"].append(
                f"Trustpilot negative Reviews (KI): {neg.get('overall_severity')} — {neg.get('summary', '')[:120]}"
            )
            if neg.get("overall_severity") == "high":
                ctx["contradiction_hints"].append(
                    "Trustpilot: negative Reviews mit Betrugsverdacht laut KI-Analyse"
                )

    if google_reviews and google_reviews.details.get("profile_found"):
        ctx["google_reviews"] = {
            "score": google_reviews.score,
            "rating": google_reviews.details.get("rating"),
            "total_reviews": google_reviews.details.get("total_reviews"),
        }
        ctx["lines"].append(
            f"Google: {google_reviews.details.get('rating')}/5, "
            f"{google_reviews.details.get('total_reviews')} Reviews, Legitimitätsscore {google_reviews.score}/12"
        )

    return ctx


def format_context_for_prompt(ctx: dict) -> str:
    if not ctx.get("lines"):
        return "Keine vorherigen Prüfergebnisse verfügbar."
    return "\n".join(f"- {line}" for line in ctx["lines"])
