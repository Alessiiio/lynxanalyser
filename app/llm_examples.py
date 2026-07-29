"""Load analyst-confirmed fraud cases for LLM prompt enrichment."""

from __future__ import annotations

from typing import Any

from app.blocklist import load_blocklist

_CATEGORY_LABELS = {
    "investment_fraud": "Anlagebetrug",
    "phishing_impersonation": "Phishing/Identitätsmissbrauch",
    "support_scam": "Support-/Tech-Betrug",
    "booking_scam": "Vorschussbetrug Buchung",
    "marketplace_scam": "Marktplatz-Betrug",
    "fake_shop": "Fake-Shop",
    "general_suspicious": "Mehrere Warnsignale",
}


def _format_yes_answers(answers: list[dict]) -> str:
    lines: list[str] = []
    for entry in answers:
        if not isinstance(entry, dict):
            continue
        if entry.get("answer") != "yes":
            continue
        q = entry.get("question")
        quote = (entry.get("evidence_quote") or entry.get("reasoning") or "").strip()
        if quote:
            lines.append(f"Q{q}: «{quote[:120]}»")
        elif q:
            lines.append(f"Q{q}: yes")
    return "; ".join(lines[:6])


def get_confirmed_fraud_examples(limit: int = 3) -> list[dict[str, Any]]:
    """Return recent blocklist entries usable as few-shot calibration."""
    entries = load_blocklist()
    examples: list[dict[str, Any]] = []
    for domain, meta in sorted(
        entries.items(),
        key=lambda item: item[1].get("confirmed_at", ""),
        reverse=True,
    ):
        if len(examples) >= limit:
            break
        category = meta.get("fraud_category", "general_suspicious")
        yes_signals = ""
        if isinstance(meta.get("llm_answers"), list):
            yes_signals = _format_yes_answers(meta["llm_answers"])
        note = (meta.get("note") or "").strip()
        examples.append({
            "domain": domain,
            "category": category,
            "category_label": _CATEGORY_LABELS.get(category, category),
            "note": note[:200],
            "yes_signals": yes_signals,
        })
    return examples


def format_fraud_examples_for_prompt(limit: int = 3) -> str:
    examples = get_confirmed_fraud_examples(limit=limit)
    if not examples:
        return "Keine internen Bestätigungsfälle verfügbar."

    lines = [
        "Intern bestätigte Betrugsfälle (Kalibrierung — nicht auf diese Domain schliessen):"
    ]
    for ex in examples:
        parts = [f"- {ex['domain']}: {ex['category_label']}"]
        if ex.get("yes_signals"):
            parts.append(f"Signale: {ex['yes_signals']}")
        if ex.get("note"):
            parts.append(f"Analyst: {ex['note']}")
        lines.append(" — ".join(parts))
    return "\n".join(lines)
