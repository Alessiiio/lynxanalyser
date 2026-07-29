"""Transaction context from fraud case analysts — used only for LLM analysis."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

EUR_TO_CHF = 0.95
USD_TO_CHF = 0.90

_VALID_CURRENCIES = frozenset({"CHF", "EUR", "USD"})


def to_chf_equivalent(amount: float, currency: str) -> float:
    cur = (currency or "CHF").upper()
    if cur == "EUR":
        return amount * EUR_TO_CHF
    if cur == "USD":
        return amount * USD_TO_CHF
    return amount


class TransactionContext(BaseModel):
    amount: float
    currency: str = "CHF"
    purpose: Optional[str] = None

    def is_present(self) -> bool:
        return self.amount is not None and self.amount > 0

    def chf_equivalent(self) -> float:
        return to_chf_equivalent(self.amount, self.currency)

    def purpose_display(self) -> str:
        text = (self.purpose or "").strip()
        return text if text else "nicht angegeben"


def parse_transaction_context(
    amount: float | None,
    currency: str | None = None,
    purpose: str | None = None,
) -> TransactionContext | None:
    if amount is None:
        return None
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    cur = (currency or "CHF").strip().upper()
    if cur not in _VALID_CURRENCIES:
        cur = "CHF"
    purpose_text = (purpose or "").strip() or None
    return TransactionContext(amount=value, currency=cur, purpose=purpose_text)
