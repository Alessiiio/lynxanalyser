"""IBAN extraction and validation for contact-page analysis."""

from __future__ import annotations

import re
from typing import Any

# Common template / documentation IBANs — not real payment targets on the scanned site.
_EXAMPLE_IBANS = frozenset({
    "CH9300762011623852957",
    "CH2100788000050871780",
    "CH5604835012345678009",
    "DE89370400440532013000",
    "DE11100100100493571790",
    "GB82WEST12345698765432",
})

_IBAN_CANDIDATE_RE = re.compile(
    r"\b([A-Z]{2})\s*(\d{2})[\s\-]*(?:[A-Z0-9][\s\-]*){11,34}\b",
    re.IGNORECASE,
)

_COUNTRY_LABELS: dict[str, str] = {
    "CH": "Schweiz",
    "DE": "Deutschland",
    "AT": "Österreich",
    "FR": "Frankreich",
    "IT": "Italien",
    "LI": "Liechtenstein",
    "NL": "Niederlande",
    "BE": "Belgien",
    "LU": "Luxemburg",
    "ES": "Spanien",
    "PT": "Portugal",
    "GB": "UK",
    "US": "USA",
    "LT": "Litauen",
    "PL": "Polen",
    "CZ": "Tschechien",
    "HU": "Ungarn",
    "RO": "Rumänien",
    "BG": "Bulgarien",
    "TR": "Türkei",
}

_TLD_COUNTRY: dict[str, str] = {
    "ch": "CH",
    "de": "DE",
    "at": "AT",
    "fr": "FR",
    "it": "IT",
    "li": "LI",
    "nl": "NL",
    "be": "BE",
    "lu": "LU",
    "es": "ES",
    "pt": "PT",
    "uk": "GB",
    "co": "GB",
    "pl": "PL",
    "cz": "CZ",
    "hu": "HU",
    "ro": "RO",
    "bg": "BG",
}


def _compact_iban(value: str) -> str:
    return re.sub(r"[\s\-]", "", value.upper())


def _format_iban(compact: str) -> str:
    return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))


def mask_iban(compact: str) -> str:
    if len(compact) <= 10:
        return _format_iban(compact)
    visible_start = compact[:6]
    visible_end = compact[-4:]
    hidden_len = len(compact) - len(visible_start) - len(visible_end)
    masked = visible_start + ("*" * hidden_len) + visible_end
    return _format_iban(masked)


def validate_iban(compact: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(
        ch if ch.isdigit() else str(ord(ch) - ord("A") + 10)
        for ch in rearranged
    )
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


def country_label(country_code: str) -> str:
    return _COUNTRY_LABELS.get(country_code.upper(), country_code.upper())


def extract_ibans_from_text(text: str, source: str) -> list[dict[str, Any]]:
    """Return deduplicated valid IBAN records found in plain text."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in _IBAN_CANDIDATE_RE.finditer(text):
        raw = match.group(0)
        compact = _compact_iban(raw)
        if compact in seen or compact in _EXAMPLE_IBANS:
            continue
        if not validate_iban(compact):
            continue
        seen.add(compact)
        country = compact[:2]
        found.append({
            "compact": compact,
            "masked": mask_iban(compact),
            "formatted": _format_iban(compact),
            "country_code": country,
            "country_label": country_label(country),
            "source": source,
        })
    return found


def infer_site_payment_locale(domain: str, swiss_plz: str | None, address_found: bool, phone: str | None) -> str:
    """Best-effort locale for IBAN plausibility (CH, DE, IT, … or UNKNOWN)."""
    domain = domain.lower().removeprefix("www.")
    if domain.endswith(".ch") or swiss_plz or address_found:
        return "CH"
    if phone:
        normalized = re.sub(r"[\s\-().]", "", phone)
        if normalized.startswith("+41") or normalized.startswith("0041"):
            return "CH"
        if re.match(r"^0[1-9]\d", normalized):
            return "CH"
    tld = domain.rsplit(".", 1)[-1]
    return _TLD_COUNTRY.get(tld, "UNKNOWN")


def is_swiss_site_context(
    domain: str,
    swiss_plz: str | None,
    address_found: bool,
    phone: str | None,
) -> bool:
    return infer_site_payment_locale(domain, swiss_plz, address_found, phone) == "CH"


def evaluate_ibans(
    ibans: list[dict[str, Any]],
    site_locale: str,
) -> dict[str, Any]:
    """
    Score IBAN plausibility relative to site context.
    Foreign IBAN on a foreign site is OK; mismatch only when Swiss context meets foreign IBAN.
    """
    flags: list[str] = []
    if not ibans:
        return {
            "points": 0,
            "max_points": 2,
            "flags": flags,
            "context_match": None,
        }

    countries = {item["country_code"] for item in ibans}
    unique_count = len({item["compact"] for item in ibans})

    if unique_count >= 2:
        flags.append(f"{unique_count} verschiedene IBANs gefunden")

    if site_locale == "CH":
        if countries == {"CH"}:
            context_match = "ch_on_swiss"
            points = 2
        elif "CH" in countries and len(countries) > 1:
            context_match = "mixed"
            points = 1
            flags.append("Schweizer und ausländische IBANs gemischt")
        else:
            context_match = "foreign_on_swiss"
            points = 0
            foreign = ", ".join(sorted(countries - {"CH"}))
            flags.append(f"Ausländische IBAN ({foreign}) bei Schweizer Website")
    elif site_locale != "UNKNOWN":
        if countries == {site_locale}:
            context_match = "local_on_local"
            points = 2
        elif "CH" in countries and site_locale != "CH":
            context_match = "ch_on_foreign"
            points = 1
        else:
            context_match = "foreign_on_foreign"
            points = 2
    else:
        context_match = "unknown_site"
        points = 1

    if unique_count >= 2 and points > 1:
        points = 1

    return {
        "points": points,
        "max_points": 2,
        "flags": flags,
        "context_match": context_match,
        "site_locale": site_locale,
        "iban_countries": sorted(countries),
    }
