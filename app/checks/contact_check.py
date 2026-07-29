from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck

from app.checks.utils import USER_AGENT, extract_swiss_plz, find_swiss_plz, strip_html_for_text, swiss_phone_plausible
from app.checks.browser_render import fetch_rendered_html
from app.checks.iban_utils import (
    evaluate_ibans,
    extract_ibans_from_text,
    infer_site_payment_locale,
    is_swiss_site_context,
)
_SUBPAGE_TIMEOUT = 8.0
_HOMEPAGE_TIMEOUT = 12.0

_PHONE_PATTERNS = [
    re.compile(r"\+41[\s\-.]?(?:\d[\s\-.]?){8,11}"),
    re.compile(r"0041[\s\-.]?(?:\d[\s\-.]?){8,11}"),
    re.compile(r"\b0[1-9]\d[\s\-.]?\d{3}[\s\-.]?\d{2}[\s\-.]?\d{2}\b"),
    re.compile(r"\b0[1-9]\d{8}\b"),
    re.compile(r"\b0800[\s\-.]?\d{2}[\s\-.]?\d{2}[\s\-.]?\d{2}\b"),
    re.compile(r"\+\d{1,3}[\s\-.]?(?:\d[\s\-.]?){7,14}"),
]

_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

_MAILTO_PATTERN = re.compile(
    r"""href=["']mailto:([^"'?]+)""",
    re.IGNORECASE,
)

_TEL_PATTERN = re.compile(
    r"""href=["']tel:([^"']+)["']""",
    re.IGNORECASE,
)

_LEGAL_LINK_PATTERN = re.compile(
    r"impressum|imprint|mentions\s+l[eé]gales|contatti|datenschutz|privacy|"
    r"nutzungsbedingungen|terms\s+of\s+(service|use)|legal\s+notice|"
    r"über\s+(uns|google)|about\s+(us|google)",
    re.IGNORECASE,
)

_CONTACT_SUBPAGE_PATTERN = re.compile(
    r"kontakt|contact|impressum|imprint|about-us|über-uns|mentions-legales|contatti",
    re.IGNORECASE,
)

_MINIMAL_PAGE_CHARS = 400

_EMAIL_SKIP_DOMAINS = {
    "sentry.io", "ingest.sentry.io", "wixpress.com", "example.com",
    "schema.org", "w3.org", "googleapis.com", "gstatic.com",
}

_EMAIL_SKIP_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "mailer-daemon"}

# Consumer mail hosts — suspicious on claimed business sites, but not alone "Critical Risk".
_FREE_WEBMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "gmx.ch", "gmx.net", "gmx.de", "bluewin.ch",
    "hotmail.com", "hotmail.ch", "outlook.com", "outlook.ch", "yahoo.com",
    "yahoo.de", "icloud.com", "me.com", "protonmail.com", "proton.me",
    "mail.com", "web.de", "aon.at", "sunrise.ch", "hispeed.ch",
})


@dataclass
class _PageFindings:
    phone: str | None = None
    phone_via_tel: bool = False
    email: str | None = None
    email_via_mailto: bool = False
    email_domain_matches: bool = False
    address_found: bool = False
    impressum_found: bool = False
    ibans: list[dict] | None = None


@dataclass
class _MergedFindings:
    phone: str | None = None
    phone_via_tel: bool = False
    phone_on_subpage: bool = False
    email: str | None = None
    email_via_mailto: bool = False
    email_domain_matches: bool = False
    email_on_subpage: bool = False
    address_found: bool = False
    address_on_subpage: bool = False
    impressum_found: bool = False
    found_on_subpage: bool = False
    subpage_url: str | None = None
    swiss_plz: str | None = None
    swiss_city: str | None = None
    phone_plausible: bool | None = None
    phone_plausibility_reason: str | None = None
    email_domain_mismatch: bool = False
    email_mismatch_severity: str | None = None  # "low" | "high" (free webmail)
    ibans: list[dict] | None = None
    iban_points: int = 0
    iban_max_points: int = 2
    iban_warning_flags: list[str] | None = None
    site_payment_locale: str | None = None
    swiss_site_context: bool = False



def _normalize_phone(phone: str) -> str:
    phone = re.sub(r"^tel:", "", phone, flags=re.IGNORECASE).strip()
    phone = re.sub(r"[^\d+\s\-().]", "", phone)
    return re.sub(r"\s+", " ", phone).strip()


def _find_phone_in_text(text: str) -> str | None:
    for pattern in _PHONE_PATTERNS:
        m = pattern.search(text)
        if m:
            return _normalize_phone(m.group(0))
    return None


def _find_tel_link(html: str) -> tuple[str | None, bool]:
    m = _TEL_PATTERN.search(html)
    if not m:
        return None, False
    phone = _normalize_phone(m.group(1))
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 9:
        return None, False
    return phone, True


def _is_contact_email(email: str) -> bool:
    email = email.lower()
    local, _, domain = email.partition("@")
    if not domain:
        return False
    if domain in _EMAIL_SKIP_DOMAINS or domain.endswith(".sentry.io"):
        return False
    if local in _EMAIL_SKIP_LOCAL_PARTS:
        return False
    if "ingest." in domain or (domain.startswith("o") and ".ingest." in domain):
        return False
    return True


def _site_brand_label(domain: str) -> str:
    """Registrable label before TLD, e.g. zweifel from zweifel.ch."""
    return domain.lower().removeprefix("www.").split(".")[0]


def _is_free_webmail(email_domain: str) -> bool:
    email_domain = email_domain.lower()
    if email_domain in _FREE_WEBMAIL_DOMAINS:
        return True
    return any(email_domain.endswith(f".{d}") for d in _FREE_WEBMAIL_DOMAINS)


def _email_domain_matches_site(email: str, domain: str) -> bool:
    email_domain = email.rsplit("@", 1)[-1].lower()
    site_domain = domain.lower().removeprefix("www.")

    if email_domain == site_domain:
        return True
    if email_domain.endswith(f".{site_domain}"):
        return True
    if site_domain.endswith(f".{email_domain}"):
        return True

    # Same corporate brand on a related domain (e.g. zweifel.ch ↔ zweifel-pomy.ch).
    brand = _site_brand_label(site_domain)
    if len(brand) >= 4:
        brand_norm = brand.replace("-", "")
        email_host_norm = email_domain.replace("-", "")
        email_label_norm = email_domain.split(".")[0].replace("-", "")
        if (
            brand_norm in email_label_norm
            or brand_norm in email_host_norm
            or email_label_norm.startswith(brand_norm)
        ):
            return True

    return False


def _find_mailto_link(html: str, domain: str) -> tuple[str | None, bool, bool]:
    for m in _MAILTO_PATTERN.finditer(html):
        email = m.group(1).strip().lower()
        if _is_contact_email(email):
            return email, True, _email_domain_matches_site(email, domain)
    return None, False, False


def _find_text_email(html: str, domain: str) -> tuple[str | None, bool]:
    candidates: list[str] = []
    for m in _EMAIL_PATTERN.finditer(html):
        email = m.group(0).lower()
        if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            continue
        candidates.append(email)

    seen: set[str] = set()
    matching: list[str] = []
    other: list[str] = []
    for email in candidates:
        if email in seen or not _is_contact_email(email):
            continue
        seen.add(email)
        if _email_domain_matches_site(email, domain):
            matching.append(email)
        else:
            other.append(email)

    if matching:
        return matching[0], True
    if other:
        return other[0], False
    return None, False


def _find_email(html: str, domain: str) -> tuple[str | None, bool, bool]:
    # mailto: links are more reliable than visible text — check them first.
    mailto_email, via_mailto, mailto_matches = _find_mailto_link(html, domain)
    if mailto_email:
        return mailto_email, via_mailto, mailto_matches

    text_email, text_matches = _find_text_email(html, domain)
    if text_email:
        return text_email, False, text_matches
    return None, False, False


def _find_swiss_address(text: str) -> bool:
    return find_swiss_plz(text)


def _find_impressum_link(html: str) -> bool:
    for m in re.finditer(r"<a\b[^>]*>([^<]*)</a>", html, re.IGNORECASE):
        if _LEGAL_LINK_PATTERN.search(m.group(1)):
            return True
    for m in re.finditer(r"""<a\b[^>]*href=["']([^"']*)["']""", html, re.IGNORECASE):
        if _LEGAL_LINK_PATTERN.search(m.group(1)):
            return True
    return False


def _same_site(base_url: str, candidate_url: str) -> bool:
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    cand_host = urlparse(candidate_url).netloc.lower().removeprefix("www.")
    return base_host == cand_host or cand_host.endswith(f".{base_host}")


def _find_contact_subpage_url(html: str, base_url: str) -> str | None:
    for m in re.finditer(
        r"""<a\b[^>]*href=["']([^"']+)["'][^>]*>([^<]*)</a>""",
        html,
        re.IGNORECASE,
    ):
        href, link_text = m.group(1).strip(), m.group(2).strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if not _CONTACT_SUBPAGE_PATTERN.search(f"{link_text} {href}"):
            continue
        full_url = urljoin(base_url, href)
        if not full_url.startswith(("http://", "https://")):
            continue
        if _same_site(base_url, full_url):
            return full_url
    return None


def _scan_page(html: str, domain: str, source: str) -> _PageFindings:
    visible_text = strip_html_for_text(html)
    findings = _PageFindings()

    tel_phone, via_tel = _find_tel_link(html)
    text_phone = _find_phone_in_text(visible_text)
    if tel_phone:
        findings.phone = tel_phone
        findings.phone_via_tel = True
    elif text_phone:
        findings.phone = text_phone

    email, via_mailto, email_matches = _find_email(html, domain)
    findings.email = email
    findings.email_via_mailto = via_mailto
    findings.email_domain_matches = email_matches

    findings.address_found = _find_swiss_address(visible_text)
    findings.impressum_found = _find_impressum_link(html)
    findings.ibans = extract_ibans_from_text(f"{visible_text}\n{html}", source)
    return findings


def _merge_findings(
    homepage: _PageFindings,
    subpage: _PageFindings | None,
    subpage_url: str | None,
) -> _MergedFindings:
    merged = _MergedFindings(
        impressum_found=homepage.impressum_found,
        subpage_url=subpage_url,
    )

    if homepage.phone:
        merged.phone = homepage.phone
        merged.phone_via_tel = homepage.phone_via_tel
    elif subpage and subpage.phone:
        merged.phone = subpage.phone
        merged.phone_via_tel = subpage.phone_via_tel
        merged.phone_on_subpage = True
        merged.found_on_subpage = True

    if homepage.email:
        merged.email = homepage.email
        merged.email_via_mailto = homepage.email_via_mailto
        merged.email_domain_matches = homepage.email_domain_matches
    elif subpage and subpage.email:
        merged.email = subpage.email
        merged.email_via_mailto = subpage.email_via_mailto
        merged.email_domain_matches = subpage.email_domain_matches
        merged.email_on_subpage = True
        merged.found_on_subpage = True

    if homepage.address_found:
        merged.address_found = True
    elif subpage and subpage.address_found:
        merged.address_found = True
        merged.address_on_subpage = True
        merged.found_on_subpage = True

    if subpage and subpage.impressum_found:
        merged.impressum_found = True

    merged.ibans = _merge_ibans(homepage, subpage)
    return merged


def _merge_ibans(homepage: _PageFindings, subpage: _PageFindings | None) -> list[dict]:
    combined: list[dict] = []
    seen: set[str] = set()
    for page in (homepage, subpage):
        if not page or not page.ibans:
            continue
        for item in page.ibans:
            compact = item.get("compact", "")
            if compact and compact not in seen:
                seen.add(compact)
                combined.append(item)
    return combined


def _phone_points(findings: _MergedFindings) -> int:
    return 4 if findings.phone else 0


def _email_points(findings: _MergedFindings) -> int:
    if not findings.email:
        return 0
    if findings.email_via_mailto:
        return 4
    return 4 if findings.email_domain_matches else 1


def _location_label(on_subpage: bool) -> str:
    return "Unterseite" if on_subpage else "Startseite"


def _build_summary(findings: _MergedFindings, score: int, max_score: int) -> str:
    parts: list[str] = []

    if findings.phone:
        source = "tel:-Link" if findings.phone_via_tel else "Text"
        parts.append(
            f"Telefon ({source}) auf {_location_label(findings.phone_on_subpage)}"
        )

    if findings.email:
        if findings.email_via_mailto:
            source = "mailto:-Link"
        elif findings.email_domain_matches:
            source = "passende Domain"
        else:
            source = "externe Domain"
        parts.append(
            f"E-Mail ({source}) auf {_location_label(findings.email_on_subpage)}"
        )

    if findings.address_found:
        parts.append(
            f"Adresse auf {_location_label(findings.address_on_subpage)}"
        )

    if findings.impressum_found:
        parts.append("Impressum/Rechtliches verlinkt")

    if findings.ibans:
        labels = ", ".join(
            f"{item['masked']} ({item['country_label']}, {item['source']})"
            for item in findings.ibans[:3]
        )
        extra = f" (+{len(findings.ibans) - 3})" if len(findings.ibans) > 3 else ""
        parts.append(f"IBAN: {labels}{extra}")

    if not parts:
        return (
            "Keine Kontaktdaten gefunden — weder auf Startseite noch auf "
            "verlinkter Kontakt-/Impressum-Seite"
        )

    return f"{'; '.join(parts)} ({score}/{max_score} pts)"


def _build_score_breakdown(findings: _MergedFindings) -> list[dict]:
    phone_pts = _phone_points(findings)
    phone_label = "Phone (tel: link)" if findings.phone_via_tel else "Phone number"
    if findings.phone_on_subpage and findings.phone:
        phone_label += " (subpage)"

    email_pts = _email_points(findings)
    if findings.email:
        if findings.email_via_mailto:
            email_label = "Email (mailto: link)"
        elif findings.email_domain_matches:
            email_label = "Email (matching domain)"
        else:
            email_label = "Email (external domain)"
        if findings.email_on_subpage:
            email_label += " (subpage)"
    else:
        email_label = "Email"

    iban_pts = findings.iban_points
    if findings.ibans:
        countries = ", ".join({item["country_code"] for item in findings.ibans})
        iban_label = f"IBAN plausibility ({countries})"
    else:
        iban_label = "IBAN plausibility"

    return [
        {"label": phone_label, "points": phone_pts, "max_points": 4},
        {"label": email_label, "points": email_pts, "max_points": 4},
        {"label": "Swiss address (PLZ)", "points": 2 if findings.address_found else 0, "max_points": 2},
        {"label": "Impressum / legal link", "points": 2 if findings.impressum_found else 0, "max_points": 2},
        {"label": iban_label, "points": iban_pts, "max_points": findings.iban_max_points},
    ]


class ContactCheck(BaseCheck):
    name = "contact"
    display_name = "Contact Information"
    max_score = 14
    tier = 2

    async def run(self, domain: str, url: str = "", **kwargs) -> CheckResult:
        target_url = url if url.startswith("http") else f"https://{domain}"

        try:
            render = await fetch_rendered_html(target_url, timeout_ms=15000)
            if not render["success"]:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.ERROR,
                    score=0,
                    max_score=self.max_score,
                    summary=render["error"] or "Failed to render homepage",
                    details={},
                )
            html = render["html"]

            homepage = _scan_page(html, domain, "Startseite")
            subpage: _PageFindings | None = None
            subpage_url: str | None = None

            if not homepage.phone and not homepage.email:
                subpage_url = _find_contact_subpage_url(html, target_url)
                if subpage_url:
                    try:
                        sub_render = await fetch_rendered_html(subpage_url, timeout_ms=10000)
                        if sub_render["success"]:
                            subpage = _scan_page(sub_render["html"], domain, "Kontakt/Impressum")
                    except Exception:
                        pass
            elif not homepage.ibans:
                subpage_url = _find_contact_subpage_url(html, target_url)
                if subpage_url:
                    try:
                        sub_render = await fetch_rendered_html(subpage_url, timeout_ms=10000)
                        if sub_render["success"]:
                            subpage = _scan_page(sub_render["html"], domain, "Kontakt/Impressum")
                    except Exception:
                        pass

            findings = _merge_findings(homepage, subpage, subpage_url)
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Failed to fetch homepage: {str(e)[:120]}",
                details={},
            )

        visible_text = strip_html_for_text(html)
        plz_info = extract_swiss_plz(visible_text)
        if plz_info:
            findings.swiss_plz, findings.swiss_city = plz_info

        if findings.phone and findings.swiss_plz:
            plaus = swiss_phone_plausible(findings.phone, findings.swiss_plz)
            findings.phone_plausible = plaus.get("plausible")
            findings.phone_plausibility_reason = plaus.get("reason")

        if findings.email and not findings.email_domain_matches:
            findings.email_domain_mismatch = True
            email_domain = findings.email.rsplit("@", 1)[-1].lower()
            findings.email_mismatch_severity = (
                "high" if _is_free_webmail(email_domain) else "low"
            )

        findings.swiss_site_context = is_swiss_site_context(
            domain,
            findings.swiss_plz,
            findings.address_found,
            findings.phone,
        )
        findings.site_payment_locale = infer_site_payment_locale(
            domain,
            findings.swiss_plz,
            findings.address_found,
            findings.phone,
        )
        iban_eval = evaluate_ibans(findings.ibans or [], findings.site_payment_locale)
        findings.iban_points = iban_eval["points"]
        findings.iban_max_points = iban_eval["max_points"]
        findings.iban_warning_flags = iban_eval["flags"]

        score = 0
        score += _phone_points(findings)
        score += _email_points(findings)
        if findings.address_found:
            score += 2
        if findings.impressum_found:
            score += 2
        score += findings.iban_points
        score = min(score, self.max_score)

        has_contact_data = any([
            findings.phone,
            findings.email,
            findings.address_found,
            findings.impressum_found,
        ])

        if not has_contact_data and len(visible_text) < _MINIMAL_PAGE_CHARS:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.NA,
                score=0,
                max_score=self.max_score,
                summary="Minimal landing page — contact check not applicable",
                details={
                    "phone_found": False,
                    "email_found": False,
                    "email_domain_matches": False,
                    "address_found": False,
                    "impressum_link_found": False,
                    "extracted_email": None,
                    "extracted_phone": None,
                    "found_on_subpage": False,
                    "subpage_url": None,
                    "score_breakdown": [],
                },
            )

        if not has_contact_data:
            status = CheckStatus.WARNING
            summary = _build_summary(findings, score, self.max_score)
        else:
            status = CheckStatus.PASSED
            summary = _build_summary(findings, score, self.max_score)

        if findings.email_domain_mismatch:
            if findings.email_mismatch_severity == "high":
                summary += " — Kontakt-E-Mail über Free-Webmail-Dienst"
            else:
                summary += " — Kontakt-E-Mail nutzt andere Domain (oft Konzern/Dienstleister)"
        if findings.phone_plausible is False:
            summary += f" — {findings.phone_plausibility_reason}"
        if findings.iban_warning_flags:
            summary += f" — {'; '.join(findings.iban_warning_flags)}"

        return CheckResult(
            name=self.name,
            display_name=self.display_name,
            status=status,
            score=score,
            max_score=self.max_score,
            summary=summary,
            details={
                "phone_found": findings.phone is not None,
                "email_found": findings.email is not None,
                "email_domain_matches": findings.email_domain_matches if findings.email else False,
                "email_domain_mismatch": findings.email_domain_mismatch,
                "email_mismatch_severity": findings.email_mismatch_severity,
                "address_found": findings.address_found,
                "impressum_link_found": findings.impressum_found,
                "extracted_email": findings.email,
                "extracted_phone": findings.phone,
                "swiss_plz": findings.swiss_plz,
                "swiss_city": findings.swiss_city,
                "phone_plausible": findings.phone_plausible,
                "phone_plausibility_reason": findings.phone_plausibility_reason,
                "found_on_subpage": findings.found_on_subpage,
                "subpage_url": findings.subpage_url,
                "ibans": findings.ibans or [],
                "iban_count": len(findings.ibans or []),
                "iban_warning_flags": findings.iban_warning_flags or [],
                "site_payment_locale": findings.site_payment_locale,
                "swiss_site_context": findings.swiss_site_context,
                "rendered_with": "playwright",
                "score_breakdown": _build_score_breakdown(findings),
            },
        )
