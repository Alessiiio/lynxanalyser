from __future__ import annotations

import asyncio
import json
import urllib.request
from urllib.request import Request
import base64

import httpx

from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck
from app.checks.utils import USER_AGENT, claims_swiss_entity
from app.checks.zefix_mutations import analyze_mutations
from app.checks.shell_takeover import detect_shell_takeover_pattern
from app.hr_network.shab_parser import build_person_timeline
import config

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; lynx/1.0)",
}

_ZEFIX_BASE = "https://www.zefix.admin.ch/ZefixPublicREST/api/v1"


def _auth_header() -> str:
    credentials = base64.b64encode(
        f"{config.ZEFIX_USERNAME}:{config.ZEFIX_PASSWORD}".encode()
    ).decode()
    return f"Basic {credentials}"


def _zefix_get(path: str) -> dict | list:
    headers = {**_HEADERS, "Authorization": _auth_header()}
    del headers["Content-Type"]
    req = Request(f"{_ZEFIX_BASE}{path}", headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _zefix_search(company_name: str, *, active_only: bool = False, max_entries: int = 10) -> list:
    """Search Zefix Public REST.

    active_only=False includes CANCELLED / deleted companies (needed for fraud work).
    """
    payload = {
        "name": company_name,
        "maxEntries": max(1, min(int(max_entries or 10), 50)),
        "languageKey": "de",
        "activeOnly": bool(active_only),
    }
    headers = {**_HEADERS, "Authorization": _auth_header()}
    req = Request(
        f"{_ZEFIX_BASE}/company/search",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    return data if isinstance(data, list) else []


def _status_key(company: dict | None) -> str:
    if not company:
        return ""
    status = company.get("status", {})
    if isinstance(status, dict):
        return str(
            status.get("id")
            or status.get("key")
            or status.get("shortDescription")
            or ""
        ).upper()
    return str(status or "").upper()


def _is_being_cancelled(company: dict | None) -> bool:
    key = _status_key(company)
    return "BEING_CANCEL" in key or "AUFLÖS" in key or "AUFLOES" in key


def _is_cancelled(company: dict | None) -> bool:
    """True for struck-off firms (not still in liquidation)."""
    key = _status_key(company)
    if not key or _is_being_cancelled(company):
        return False
    return key == "CANCELLED" or any(
        tok in key
        for tok in ("DELETE", "GELÖSCHT", "GELOESCHT", "RADIERT", "RADIÉ", "RADIE")
    )


def _is_active(company: dict) -> bool:
    key = _status_key(company)
    if _is_cancelled(company) or _is_being_cancelled(company):
        return False
    return key in ("ACTIVE", "INSCRIT", "EINGETRAGEN", "ISCRITTA", "A", "EXISTIEREND", "AKTIV")


def _format_uid(uid: str) -> str:
    digits = "".join(c for c in uid if c.isdigit())
    if len(digits) == 9:
        return f"CHE-{digits[:3]}.{digits[3:6]}.{digits[6:9]}"
    return uid


async def _fetch_homepage_html(url: str) -> str:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return resp.text


class ZefixCheck(BaseCheck):
    name = "zefix"
    display_name = "Swiss Company (Zefix)"
    max_score = 10
    tier = 2

    async def run(self, domain: str, company_name: str = None, url: str = "", **kwargs) -> CheckResult:
        if not company_name:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.NA,
                score=0,
                max_score=self.max_score,
                summary="No company name provided — enter one above to check",
                details={"searched": False},
            )

        if not config.ZEFIX_USERNAME or not config.ZEFIX_PASSWORD:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.SKIPPED,
                score=0,
                max_score=self.max_score,
                summary="Zefix credentials not configured",
                details={
                    "skipped": True,
                    "setup_url": "https://www.zefix.admin.ch/ZefixPublicREST/",
                    "note": "Register for free API access at zefix.admin.ch and set ZEFIX_USERNAME + ZEFIX_PASSWORD in .env",
                },
            )

        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(_zefix_search, company_name),
                timeout=20,
            )

            if not data:
                target_url = url if url.startswith("http") else f"https://{domain}"
                claims = False
                try:
                    html = await _fetch_homepage_html(target_url)
                    claims = claims_swiss_entity(html)
                except Exception:
                    pass

                if not claims:
                    return CheckResult(
                        name=self.name,
                        display_name=self.display_name,
                        status=CheckStatus.NA,
                        score=0,
                        max_score=self.max_score,
                        summary=(
                            "Keine Schweizer Rechtsform/Adresse erkennbar — "
                            "Zefix-Check nicht anwendbar (vermutlich ausländische Firma)"
                        ),
                        details={
                            "searched": True,
                            "query": company_name,
                            "results": 0,
                            "claims_swiss_entity": False,
                        },
                    )

                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.WARNING,
                    score=0,
                    max_score=self.max_score,
                    summary=(
                        "Webseite nennt Schweizer Rechtsform/Adresse, aber keine passende "
                        "Firma im Handelsregister gefunden — bitte manuell prüfen"
                    ),
                    details={
                        "searched": True,
                        "query": company_name,
                        "results": 0,
                        "claims_swiss_entity": True,
                    },
                )

            best = next((c for c in data if _is_active(c)), data[0])

            status_raw = best.get("status", {})
            if isinstance(status_raw, dict):
                company_status = status_raw.get("shortDescription", status_raw.get("key", "UNKNOWN"))
            else:
                company_status = str(status_raw)

            is_active = _is_active(best)
            base_score = 10 if is_active else 2
            status = CheckStatus.PASSED if is_active else CheckStatus.WARNING

            company_name_found = best.get("name", "Unknown")
            uid_raw = str(best.get("uid", "") or best.get("chid", ""))
            uid_formatted = _format_uid(uid_raw)
            ehraid = best.get("ehraid")

            canton_obj = best.get("canton", {})
            canton = canton_obj.get("id", "") if isinstance(canton_obj, dict) else str(canton_obj)

            zefix_url = (
                f"https://www.zefix.admin.ch/en/search/entity/list/firm/{ehraid}"
                if ehraid else "https://www.zefix.admin.ch"
            )

            mutation_info: dict = {}
            shell_pattern: dict = {}
            fraud_network_match: dict = {}
            score_breakdown: list[dict] = []
            warning_flags: list[str] = []
            detail: dict | None = None

            if ehraid:
                try:
                    detail = await asyncio.wait_for(
                        asyncio.to_thread(_zefix_get, f"/company/ehraid/{ehraid}"),
                        timeout=20,
                    )
                    if isinstance(detail, dict):
                        mutation_info = analyze_mutations(
                            detail.get("sogcPub"),
                            old_names=detail.get("oldNames"),
                            has_taken_over=detail.get("hasTakenOver"),
                            was_taken_over_by=detail.get("wasTakenOverBy"),
                        )
                        shell_pattern = detect_shell_takeover_pattern(detail.get("sogcPub"))
                except Exception:
                    mutation_info = {"mutation_analysis": "SHAB-Daten nicht abrufbar"}
                    detail = None

            if is_active:
                score_breakdown.append({
                    "label": "Aktive Firma im Handelsregister",
                    "points": 10,
                    "max_points": 10,
                })
            else:
                score_breakdown.append({
                    "label": "Firma gefunden (inaktiver Status)",
                    "points": 2,
                    "max_points": 10,
                })

            if mutation_info.get("score_breakdown"):
                score_breakdown.extend(mutation_info["score_breakdown"])

            adjustment = mutation_info.get("score_adjustment", 0)

            if shell_pattern.get("pattern_detected") and shell_pattern.get("confidence") == "high":
                adjustment -= 3
                score_breakdown.append({
                    "label": "Shell-Takeover-Muster (altes Unternehmen + Organwechsel)",
                    "points": -3,
                    "max_points": 3,
                })
                warning_flags.append(
                    "Altes Unternehmen mit plötzlichem Organwechsel — "
                    "mögliches Übernahme-/Money-Mule-Muster"
                )
                # Preventive watchlist intake (best-effort, never fail the check)
                try:
                    from app.hr_network.watch_intake import intake_from_shell_takeover
                    if isinstance(detail, dict):
                        await intake_from_shell_takeover(detail, shell_pattern)
                except Exception:
                    pass
            elif shell_pattern.get("pattern_detected") and shell_pattern.get("confidence") == "medium":
                adjustment -= 1
                score_breakdown.append({
                    "label": "Möglicher Shell-Takeover (mittel)",
                    "points": -1,
                    "max_points": 1,
                })
                warning_flags.append(shell_pattern.get("reason") or "Möglicher Shell-Takeover")

            # Fraud-network / watched-person cross-check
            try:
                from app.hr_network.person_monitoring import match_company_against_watchlist

                officer_slugs: list[str] = []
                if isinstance(detail, dict):
                    for p in build_person_timeline(detail.get("sogcPub")):
                        if p.get("status") == "current" and p.get("id"):
                            officer_slugs.append(p["id"])
                fraud_network_match = await match_company_against_watchlist(
                    ehraid=int(ehraid) if ehraid else None,
                    uid=uid_formatted,
                    officer_slugs=officer_slugs,
                )
                if fraud_network_match.get("matched"):
                    hit = (fraud_network_match.get("hits") or [{}])[0]
                    src = hit.get("source_company_name") or "Fraud-Watchlist"
                    warning_flags.append(
                        f"Verantwortliche Person bereits auf Fraud-Watchlist "
                        f"(bekannt aus: {src})"
                    )
                    adjustment -= 4
                    score_breakdown.append({
                        "label": "Fraud-Watchlist-Treffer",
                        "points": -4,
                        "max_points": 4,
                    })
                    status = CheckStatus.WARNING
            except Exception:
                fraud_network_match = {"matched": False, "error": "watchlist_check_failed"}

            score = max(0, min(self.max_score, base_score + adjustment))

            if mutation_info.get("warning_flags"):
                warning_flags = list(dict.fromkeys(
                    list(mutation_info["warning_flags"]) + warning_flags
                ))
                if score < base_score and status == CheckStatus.PASSED:
                    status = CheckStatus.WARNING
            elif warning_flags and status == CheckStatus.PASSED and score < base_score:
                status = CheckStatus.WARNING

            summary = f"Found: {company_name_found} — {company_status}"
            if mutation_info.get("mutation_analysis"):
                summary = f"{summary} · {mutation_info['mutation_analysis']}"
            if shell_pattern.get("pattern_detected"):
                summary = f"{summary} · Shell-Takeover: {shell_pattern.get('confidence')}"

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=summary,
                details={
                    "name": company_name_found,
                    "uid": uid_formatted,
                    "status": company_status,
                    "canton": canton,
                    "zefix_url": zefix_url,
                    "results_count": len(data),
                    "ehraid": ehraid,
                    "score_breakdown": score_breakdown,
                    "warning_flags": warning_flags,
                    "mutation_analysis": mutation_info.get("mutation_analysis"),
                    "publication_count": mutation_info.get("publication_count"),
                    "latest_mutation_date": mutation_info.get("latest_mutation_date"),
                    "days_since_last_mutation": mutation_info.get("days_since_last_mutation"),
                    "recent_publications": mutation_info.get("recent_publications", []),
                    "is_new_registration_only": mutation_info.get("is_new_registration_only"),
                    "is_young_company": mutation_info.get("is_young_company"),
                    "shell_takeover_pattern": shell_pattern or None,
                    "fraud_network_match": fraud_network_match or None,
                },
            )
        except Exception as e:
            err = str(e)
            if "403" in err:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.ERROR,
                    score=0,
                    max_score=self.max_score,
                    summary="Zefix API rejected credentials (HTTP 403)",
                    details={
                        "note": "Check your ZEFIX_USERNAME and ZEFIX_PASSWORD in .env",
                        "setup_url": "https://www.zefix.admin.ch/ZefixPublicREST/",
                    },
                )
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"Zefix lookup failed: {err[:120]}",
                details={},
            )
