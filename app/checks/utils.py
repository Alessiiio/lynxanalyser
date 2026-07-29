"""Shared helpers for Swiss text/HTML pattern detection across checks."""

from __future__ import annotations

import re

SWISS_PLZ_PATTERN = re.compile(
    r"\b([1-9]\d{3})\s+([A-ZÄÖÜ][A-Za-zäöüéèàâêîôûç\-]+(?:\s+[A-ZÄÖÜ][A-Za-zäöüéèàâêîôûç\-]+)*)\b"
)

SWISS_LEGAL_FORM_PATTERN = re.compile(r"\b(?:AG|GmbH)\b", re.IGNORECASE)

SWISS_ENTITY_CLAIM_PATTERN = re.compile(
    r"schweizer\s+unternehmen|swiss\s+company|sitz\s+in\s+der\s+schweiz",
    re.IGNORECASE,
)

USER_AGENT = "Mozilla/5.0 (compatible; Lynx/1.0)"


def strip_html_for_text(html: str) -> str:
    html = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</(script|style|noscript)>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def find_swiss_plz(text: str) -> bool:
    return bool(SWISS_PLZ_PATTERN.search(text))


def extract_swiss_plz(text: str) -> tuple[str, str] | None:
    m = SWISS_PLZ_PATTERN.search(text)
    if not m:
        return None
    return m.group(1), m.group(2)


def swiss_phone_plausible(phone: str, plz: str | None = None) -> dict:
    """
    Basic Swiss phone/PLZ plausibility check.
    Returns {plausible: bool|None, reason: str}.
    """
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("41"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]

    if len(digits) < 9:
        return {"plausible": False, "reason": "Telefonnummer zu kurz"}

    # Obvious non-Swiss country codes in original string
    if phone.strip().startswith("+") and not phone.strip().startswith("+41"):
        if plz:
            return {"plausible": False, "reason": "Ausländische Vorwahl bei Schweizer PLZ"}
        return {"plausible": None, "reason": "Ausländische Vorwahl"}

    if plz:
        plz_int = int(plz)
        if plz_int < 1000 or plz_int > 9658:
            return {"plausible": False, "reason": "Ungültige Schweizer PLZ"}

    return {"plausible": True, "reason": "Schweizer Nummernformat plausibel"}


def claims_swiss_entity(html: str) -> bool:
    text = strip_html_for_text(html)
    if SWISS_LEGAL_FORM_PATTERN.search(text):
        return True
    if SWISS_PLZ_PATTERN.search(text):
        return True
    if SWISS_ENTITY_CLAIM_PATTERN.search(text):
        return True
    return False
