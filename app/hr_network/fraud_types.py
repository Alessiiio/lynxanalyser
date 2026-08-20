"""Betrugsarten laut Broschüre «Betrug entdecken» — stabile Codes + deutsche Labels."""

from __future__ import annotations

# Canonical codes (DB + API). Order matches brochure TOC.
FRAUD_TYPES: tuple[str, ...] = (
    "investment_scam",
    "refund_scam",
    "romance_scam",
    "whatsapp_scam",
    "phone_scam",
    "support_scam",
    "phishing",
    "malware",
    "shopping_scam",
    "advance_fee_scam",
    "ceo_scam",
    "grandchild_scam",
    "inheritance_scam",
    "other",
)

FRAUD_TYPE_LABELS: dict[str, str] = {
    "investment_scam": "Investment Scam",
    "refund_scam": "Refund Scam",
    "romance_scam": "Romance Scam",
    "whatsapp_scam": "WhatsApp-Betrug",
    "phone_scam": "Telefonbetrug",
    "support_scam": "Support Scam",
    "phishing": "Phishing",
    "malware": "Malware",
    "shopping_scam": "Shopping Scam",
    "advance_fee_scam": "Vorschussbetrug",
    "ceo_scam": "CEO Scam",
    "grandchild_scam": "Enkeltrickbetrug",
    "inheritance_scam": "Erbschaftsbetrug",
    "other": "Sonstiges",
    # Legacy (pre-brochure); normalize on read/migrate
    "fake_bank_employee": "Telefonbetrug",
}

# Old stored values → canonical code
FRAUD_TYPE_ALIASES: dict[str, str] = {
    "fake_bank_employee": "phone_scam",
}


def normalize_fraud_type(raw: str | None) -> str | None:
    """Map legacy aliases to canonical codes; unknown non-empty values stay as-is until validated."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return FRAUD_TYPE_ALIASES.get(text, text)


def is_valid_fraud_type(raw: str | None) -> bool:
    code = normalize_fraud_type(raw)
    return bool(code) and code in FRAUD_TYPES


def fraud_type_label(raw: str | None) -> str:
    """German label for UI/exports; empty → em dash."""
    if not raw or not str(raw).strip():
        return "—"
    code = normalize_fraud_type(raw) or str(raw).strip()
    return FRAUD_TYPE_LABELS.get(code) or FRAUD_TYPE_LABELS.get(str(raw).strip()) or str(raw).strip()


def fraud_type_choices() -> list[dict[str, str]]:
    """Options for Akte select / Fallliste filter."""
    return [{"value": code, "label": FRAUD_TYPE_LABELS[code]} for code in FRAUD_TYPES]
