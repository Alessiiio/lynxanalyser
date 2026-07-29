from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

import httpx

import config
from app.models import CheckResult, CheckStatus
from app.checks.base import BaseCheck
from app.checks.llm_pages import gather_llm_content
from app.checks.llm_context import format_context_for_prompt
from app.llm_examples import format_fraud_examples_for_prompt
from app.transaction_context import TransactionContext

_MAX_SCORE = 16

_QUESTION_PENALTIES: dict[int, int] = {
    1: 4,
    2: 1,
    3: 5,
    4: 2,
    5: 2,
    6: 1,
    7: 3,
    8: 1,
    9: 1,
    10: 2,
    11: 3,
    12: 5,
    13: 6,
}

_QUESTION_COUNT = 12

_QUESTION_TEXTS: dict[int, str] = {
    1: "Werden konkrete Geldbeträge, Renditen oder Gewinne versprochen, die unrealistisch oder übertrieben wirken?",
    2: 'Wird zeitlicher Druck oder künstliche Verknappheit erzeugt (z.B. "nur noch heute", "limitiertes Angebot")?',
    3: "Gibt sich die Seite als eine bestimmte, bekannte Institution aus (Bank, Behörde, bekanntes Unternehmen), ohne nachweislich diese zu sein?",
    4: "Ist NICHT erkennbar, was das Unternehmen konkret verkauft oder anbietet (vages Geschäftsmodell)?",
    5: "Fehlen überprüfbare, vollständige Kontaktangaben (nicht nur ob ein Impressum-Link existiert, sondern ob der Inhalt plausibel und vollständig wirkt)?",
    6: "Wirken Kundenstimmen/Testimonials generisch, austauschbar oder unbelegt?",
    7: "Wird zu unüblichen oder schwer nachverfolgbaren Zahlungswegen aufgefordert (Kryptowährung, Vorauskasse, Geldtransfer-Dienste)?",
    8: "Gibt es sprachliche Auffälligkeiten, die auf eine maschinelle Übersetzung oder nicht-lokale Urheberschaft hindeuten?",
    9: "Wirkt der Gesamtton reisserisch oder aggressiv bewerbend statt sachlich-geschäftlich?",
    10: "Fehlt ein klar erkennbarer rechtlicher Rahmen (AGB, Datenschutzerklärung, Widerrufsrecht), der bei einem seriösen Angebot zu erwarten wäre?",
    11: 'Erzeugt die Seite eine künstliche Alarm-/Panikstimmung über angebliche technische Probleme (z.B. "Ihr Computer ist infiziert", "Virus gefunden", "Ihr Konto wurde gesperrt", gefälschte System-Warnmeldungen)?',
    12: 'Wird der Nutzer aufgefordert, eine Telefonnummer anzurufen, eine Fernzugriffs-Software herunterzuladen (z.B. AnyDesk, TeamViewer), oder einem "Support-Mitarbeiter" Zugriff auf das eigene Gerät zu gewähren?',
}

_BOOKING_KEYWORDS = (
    "ferienwohnung", "apartment", "buchung", "unterkunft", "vermietung",
    "booking", "rental", "airbnb",
)
_MARKETPLACE_KEYWORDS = (
    "inserat", "kleinanzeige", "verkäufer", "verkaufer", "käufer", "kaufer",
    "marketplace", "tutti", "ricardo",
)


def _build_transaction_context_block(transaction: TransactionContext) -> str:
    chf_equiv = transaction.chf_equivalent()
    return (
        "TRANSAKTIONSKONTEXT (vom Analysten eingegeben):\n"
        f"- Überwiesener Betrag: {transaction.amount:g} {transaction.currency} "
        f"(CHF-Äquivalent: ca. {chf_equiv:,.0f} CHF)\n"
        f"- Verwendungszweck: {transaction.purpose_display()}\n\n"
        "Behalte diesen Kontext bei der Beantwortung aller Fragen im Hinterkopf, "
        "insbesondere bei Frage 13.\n\n"
    )


def _build_question_13_text(transaction: TransactionContext) -> str:
    purpose = transaction.purpose_display()
    return (
        f"Stimmt der Transaktionsbetrag von {transaction.amount:g} {transaction.currency} "
        f"mit dem überein, was diese Webseite plausiblerweise anbietet? "
        f"Beachte dabei: "
        f"Ein Betrag über 10'000 CHF-Äquivalent an einen normalen Onlineshop "
        f"(Produkte unter 5'000 CHF) ist verdächtig. "
        f"Jeder Betrag an eine Seite, die Finanzdienstleistungen/Investitionen anbietet "
        f"aber keine regulatorischen Angaben zeigt, ist verdächtig. "
        f"Wenn Verwendungszweck vorhanden: Stimmt der genannte Zweck ({purpose}) "
        f"mit dem Angebot der Seite überein? "
        f"Falls der Verwendungszweck «nicht angegeben» ist: Beantworte basierend nur "
        f"auf Betragshöhe und Geschäftsmodell der Seite."
    )


_SWISS_EXAMPLES = """
Swiss fraud examples (use as calibration, not as assumptions):
- "10% monatlich garantiert", "Verdopplung in 30 Tagen" → Q1 yes (quote the exact phrase)
- Fake PostFinance/UBS/Raiffeisen login or branding → Q3 yes
- Only WhatsApp/Telegram, no Swiss address → Q5 yes
- Impressum with PLZ, phone, company name → Q5 no
- Links to Impressum + Datenschutz + AGB → Q10 no
- Bitcoin/USDT only for "investment" → Q7 yes
- Vague "Finanzberatung" with no product detail → Q4 yes (unless Zefix confirms a registered company)
- "Ihr Computer ist infiziert", fake Microsoft/Apple virus popup → Q11 yes
- "Rufen Sie sofort an", AnyDesk/TeamViewer download → Q12 yes
"""

_FACTS_PROMPT = """You are a Swiss financial fraud analyst. Extract ONLY factual observations from the website content below. Do not infer or guess. Use null or empty arrays when information is missing.

Cross-check results from other tools (trust these when they conflict with ambiguous page text):
{cross_context}

Respond ONLY with JSON in this format:
{{
  "claimed_company": "<string or null>",
  "claimed_licenses_or_regulators": ["FINMA", "VQF"],
  "promised_returns_or_amounts": ["10% monatlich"],
  "pressure_or_scarcity_phrases": ["nur noch heute"],
  "impersonated_brands": ["UBS", "PostFinance"],
  "payment_methods_mentioned": ["Bitcoin", "USDT"],
  "testimonials_or_customer_quotes": false,
  "languages_detected": ["de"],
  "legal_pages_referenced": ["Impressum", "Datenschutz", "AGB"],
  "contact_details_found": {{"phone": false, "email": false, "address": false}},
  "business_offering_summary": "<one sentence>",
  "tone": "promotional|neutral|professional"
}}

Website URL: {url}
Pages analyzed: {page_count}

Content:
---
{content}
---

JSON only, no other text."""

_QUESTIONS_PROMPT = """You are a Swiss financial fraud analyst specializing in facade and investment fraud targeting Swiss consumers.

Answer 12 fraud-detection questions using ONLY the extracted facts JSON and the content excerpts below. Do not invent facts. Answer each question independently.

{swiss_examples}

{fraud_examples}

Cross-check context (deterministic results — do NOT contradict these):
{cross_context}

Extracted facts (Pass 1):
{facts_json}

Content excerpts:
---
{content}
---

Rules:
- For "yes": you MUST include evidence_quote — an exact substring (minimum 10 characters) copied verbatim from the content that proves the risk signal.
- If you cannot quote exact supporting text, answer "unclear" instead of "yes".
- For "no" or "unclear": evidence_quote must be empty string "".

Questions:
1. """ + _QUESTION_TEXTS[1] + """
2. """ + _QUESTION_TEXTS[2] + """
3. """ + _QUESTION_TEXTS[3] + """
4. """ + _QUESTION_TEXTS[4] + """
5. """ + _QUESTION_TEXTS[5] + """
6. """ + _QUESTION_TEXTS[6] + """
7. """ + _QUESTION_TEXTS[7] + """
8. """ + _QUESTION_TEXTS[8] + """
9. """ + _QUESTION_TEXTS[9] + """
10. """ + _QUESTION_TEXTS[10] + """
11. """ + _QUESTION_TEXTS[11] + """
12. """ + _QUESTION_TEXTS[12] + """

Respond ONLY with JSON:
{{
  "answers": [
    {{"question": 1, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 2, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 3, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 4, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 5, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 6, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 7, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 8, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 9, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 10, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 11, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}},
    {{"question": 12, "answer": "yes|no|unclear", "reasoning": "<max 15 words>", "evidence_quote": ""}}
  ],
  "content_type": "<kurze Beschreibung, was die Seite anzubieten scheint>"
}}

JSON only, no other text."""


_BLOCKING_HTTP_CODES = {403, 429, 503}

_BLOCKING_HTML_PATTERNS = [
    "cloudflare",
    "checking your browser",
    "captcha",
    "access denied",
    "just a moment",
    "ddos protection",
    "please verify you are human",
]

_BLOCKED_SUMMARY = (
    "Seite vermutlich durch Bot-Schutz blockiert oder JS-Rendering — "
    "Analyse möglicherweise unzuverlässig (siehe Diagnose unten)"
)

_VALID_ANSWERS = frozenset({"yes", "no", "unclear"})

_LEGAL_SECTION_KEYWORDS = frozenset({
    "impressum", "imprint", "datenschutz", "privacy", "agb", "terms", "legal", "widerruf",
})


def _extract_text(html: str, max_chars: int = 4000) -> str:
    html = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</(script|style|noscript)>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _detect_blocking(html: str, status_code: int) -> dict:
    reasons: list[str] = []

    if status_code in _BLOCKING_HTTP_CODES:
        reasons.append(f"HTTP status {status_code}")

    html_lower = html.lower()
    for pattern in _BLOCKING_HTML_PATTERNS:
        if pattern in html_lower:
            reasons.append(f"Bot protection pattern: {pattern}")

    raw_html_length = len(html)
    extracted_text = _extract_text(html, max_chars=1_000_000)
    extracted_text_length = len(extracted_text)
    text_ratio_percent = round(
        (extracted_text_length / raw_html_length * 100) if raw_html_length > 0 else 0.0,
        1,
    )

    if (
        raw_html_length > 500
        and text_ratio_percent < 3.0
        and extracted_text_length < 300
    ):
        reasons.append(
            f"Low text ratio ({text_ratio_percent}% with only {extracted_text_length} chars — page may still be blocked)"
        )

    return {
        "likely_blocked": bool(reasons),
        "reasons": reasons,
        "raw_html_length": raw_html_length,
        "extracted_text_length": extracted_text_length,
        "text_ratio_percent": text_ratio_percent,
    }


def _normalize_text_for_match(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _validate_evidence_quote(quote: str, corpus: str) -> bool:
    if not quote or len(quote.strip()) < 10:
        return False
    normalized_quote = _normalize_text_for_match(quote)
    normalized_corpus = _normalize_text_for_match(corpus)
    if len(normalized_quote) < 8:
        return False
    if normalized_quote in normalized_corpus:
        return True
    if len(normalized_quote) > 40 and normalized_quote[:40] in normalized_corpus:
        return True
    return False


def _normalize_answer_value(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _VALID_ANSWERS:
            return normalized
    return "unclear"


def _normalize_answers(raw: object, include_q13: bool = False) -> list[dict]:
    """Normalize question answers (12 by default, 13 when transaction context is present)."""
    max_question = 13 if include_q13 else _QUESTION_COUNT
    by_question: dict[int, dict] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            q = item.get("question")
            if isinstance(q, int) and 1 <= q <= max_question:
                reasoning = item.get("reasoning", "")
                if not isinstance(reasoning, str):
                    reasoning = str(reasoning)
                evidence = item.get("evidence_quote", "")
                if not isinstance(evidence, str):
                    evidence = str(evidence) if evidence else ""
                by_question[q] = {
                    "question": q,
                    "answer": _normalize_answer_value(item.get("answer")),
                    "reasoning": reasoning.strip()[:120],
                    "evidence_quote": evidence.strip()[:300],
                }

    answers: list[dict] = []
    for q in range(1, max_question + 1):
        if q in by_question:
            answers.append(by_question[q])
        else:
            answers.append({
                "question": q,
                "answer": "unclear",
                "reasoning": "Keine Antwort vom Modell",
                "evidence_quote": "",
            })
    return answers


def _apply_evidence_validation(answers: list[dict], corpus: str) -> list[dict]:
    validated: list[dict] = []
    for entry in answers:
        updated = dict(entry)
        if updated.get("question") == 13:
            if updated["answer"] == "yes":
                updated["evidence_valid"] = True
            else:
                updated["evidence_valid"] = None
            validated.append(updated)
            continue
        if updated["answer"] == "yes":
            quote = updated.get("evidence_quote", "")
            if _validate_evidence_quote(quote, corpus):
                updated["evidence_valid"] = True
            else:
                updated["evidence_valid"] = False
                updated["answer"] = "unclear"
                prev = updated.get("reasoning", "")
                updated["reasoning"] = f"{prev} [Kein gültiges Zitat]"[:120]
        else:
            updated["evidence_valid"] = None
        validated.append(updated)
    return validated


def _count_legal_sections(sections: list[dict]) -> int:
    count = 0
    for section in sections:
        label = (section.get("label") or "").lower()
        if any(kw in label for kw in _LEGAL_SECTION_KEYWORDS):
            count += 1
    return count


def _apply_deterministic_overrides(
    answers: list[dict],
    check_context: dict,
    sections: list[dict],
) -> tuple[list[dict], list[str]]:
    """Couple Q5/Q10 with Contact check and other deterministic signals."""
    by_q = {a["question"]: dict(a) for a in answers}
    notes: list[str] = []

    contact = check_context.get("contact") or {}
    contact_score = contact.get("score", 0)
    impressum = contact.get("impressum_found", False)
    has_phone = contact.get("phone", False)
    has_email = contact.get("email", False)
    has_address = contact.get("address", False)
    legal_sections = _count_legal_sections(sections)

    if impressum and legal_sections >= 1 and by_q.get(10, {}).get("answer") == "yes":
        by_q[10]["answer"] = "no"
        by_q[10]["reasoning"] = "Contact-Check + Rechtsseiten: Impressum/Datenschutz gefunden"
        by_q[10]["evidence_valid"] = None
        by_q[10]["overridden"] = True
        notes.append("Q10: Rechtliche Unterseiten deterministisch gefunden")

    if contact_score >= 8 and has_phone and has_email and by_q.get(5, {}).get("answer") == "yes":
        by_q[5]["answer"] = "no"
        by_q[5]["reasoning"] = "Contact-Check: Telefon + E-Mail vollständig gefunden"
        by_q[5]["evidence_valid"] = None
        by_q[5]["overridden"] = True
        notes.append("Q5: Kontaktdaten vom Contact-Check bestätigt")

    if contact_score >= 6 and impressum and has_address and by_q.get(5, {}).get("answer") == "yes":
        by_q[5]["answer"] = "no"
        by_q[5]["reasoning"] = "Contact-Check: Impressum + Adresse gefunden"
        by_q[5]["evidence_valid"] = None
        by_q[5]["overridden"] = True
        notes.append("Q5: Impressum und Adresse deterministisch vorhanden")

    zefix = check_context.get("zefix") or {}
    if zefix.get("company") and by_q.get(4, {}).get("answer") == "yes":
        by_q[4]["answer"] = "unclear"
        by_q[4]["reasoning"] = "Zefix: Registrierter Firmeneintrag — Modell nicht als vage gewertet"
        by_q[4]["evidence_valid"] = None
        by_q[4]["overridden"] = True
        notes.append("Q4: Zefix-Firmeneintrag widerspricht «vages Geschäftsmodell»")

    finma = check_context.get("finma") or {}
    if finma.get("listed"):
        notes.append("FINMA-Warnliste: Domain/Unternehmen offiziell gemeldet")

    iscan = check_context.get("iscan") or {}
    if iscan.get("listed"):
        notes.append("I-SCAN-Warnliste: Domain/Unternehmen international gemeldet")

    return [by_q.get(a["question"], a) for a in answers], notes


def _content_matches_keywords(corpus: str, keywords: tuple[str, ...]) -> bool:
    lower = corpus.lower()
    return any(kw in lower for kw in keywords)


def _derive_fraud_category(
    by_q: dict[int, str],
    yes_count: int,
    confidence: str,
    corpus: str,
) -> str:
    """Map answers to fraud_category (ZKB-inspired taxonomy + existing categories)."""
    if by_q.get(3) == "yes":
        return "phishing_impersonation"
    if by_q.get(12) == "yes":
        return "support_scam"
    if by_q.get(11) == "yes" and by_q.get(12) != "yes":
        return "support_scam"
    if by_q.get(1) == "yes" and by_q.get(7) == "yes":
        return "investment_fraud"
    if by_q.get(7) == "yes" and by_q.get(4) == "yes":
        if _content_matches_keywords(corpus, _BOOKING_KEYWORDS):
            return "booking_scam"
        if _content_matches_keywords(corpus, _MARKETPLACE_KEYWORDS):
            return "marketplace_scam"
        return "fake_shop"
    if by_q.get(1) == "yes":
        category = "investment_fraud"
    elif yes_count >= 3:
        category = "general_suspicious"
    elif yes_count == 0 and confidence != "low":
        category = "none_detected"
    else:
        category = "unclear"

    if by_q.get(13) == "yes" and category in ("none_detected", "unclear", "general_suspicious"):
        return "transaction_mismatch"
    return category


def _answer_map(answers: list[dict]) -> dict[int, str]:
    return {a["question"]: a["answer"] for a in answers}


def _validated_yes_count(answers: list[dict]) -> int:
    return sum(
        1 for a in answers
        if a.get("answer") == "yes" and a.get("evidence_valid") is True
    )


def _calculate_score_from_answers(
    answers: list[dict],
    corpus: str = "",
) -> dict[str, Any]:
    """Deterministic score, confidence, and category from the question catalog."""
    by_q = _answer_map(answers)
    max_question = 13 if any(a.get("question") == 13 for a in answers) else _QUESTION_COUNT
    score = _MAX_SCORE
    yes_count = 0
    validated_yes = 0
    unclear_count = 0

    for q in range(1, max_question + 1):
        entry = next((a for a in answers if a["question"] == q), None)
        ans = by_q.get(q, "unclear")
        if ans == "yes":
            yes_count += 1
            if entry and entry.get("evidence_valid") is True:
                score -= _QUESTION_PENALTIES.get(q, 0)
                validated_yes += 1
            else:
                unclear_count += 1
        elif ans == "unclear":
            unclear_count += 1

    score = max(score, 0)

    if unclear_count <= 1:
        confidence = "high"
    elif unclear_count <= 4:
        confidence = "medium"
    else:
        confidence = "low"

    fraud_category = _derive_fraud_category(by_q, yes_count, confidence, corpus)

    if score >= 11:
        status = CheckStatus.PASSED
    elif score >= 5:
        status = CheckStatus.WARNING
    else:
        status = CheckStatus.FAILED

    return {
        "score": score,
        "confidence": confidence,
        "fraud_category": fraud_category,
        "yes_count": yes_count,
        "validated_yes_count": validated_yes,
        "unclear_count": unclear_count,
        "status": status,
    }


def _build_score_breakdown(answers: list[dict]) -> list[dict]:
    """Per-question breakdown for UI transparency."""
    items: list[dict] = []
    for entry in answers:
        if entry.get("answer") != "yes" or entry.get("evidence_valid") is not True:
            continue
        q = entry["question"]
        penalty = _QUESTION_PENALTIES.get(q, 0)
        label_source = _QUESTION_TEXTS.get(q) or f"Frage {q}"
        label = label_source.split("?")[0]
        if len(label) > 48:
            label = label[:45] + "…"
        items.append({
            "label": f"✗ {label} (−{penalty} Pkt.)",
            "points": 0,
            "max_points": penalty,
        })
    if not items:
        items.append({
            "label": "Keine belegten Risikosignale erkannt",
            "points": _MAX_SCORE,
            "max_points": _MAX_SCORE,
        })
    return items


def _build_summary(
    score: int,
    fraud_category: str,
    content_type: str,
    validated_yes: int,
) -> str:
    type_part = f" — {content_type}" if content_type else ""
    if fraud_category == "none_detected":
        return f"Keine Betrugsmuster erkannt ({score}/{_MAX_SCORE}){type_part}"
    if fraud_category == "unclear":
        return f"Uneindeutige KI-Einschätzung ({score}/{_MAX_SCORE}){type_part}"
    if score >= 11:
        return f"Geringes Risiko trotz Einzelsignalen ({score}/{_MAX_SCORE}, {validated_yes}× belegt){type_part}"
    if score >= 5:
        return f"Mehrere Warnsignale ({score}/{_MAX_SCORE}, {validated_yes}× belegt){type_part}"
    return f"Starkes Betrugsrisiko ({score}/{_MAX_SCORE}, {validated_yes}× belegt){type_part}"


_JSON_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Respond with ONLY valid JSON, no markdown formatting, no extra text."
)


class OllamaConnectionError(Exception):
    """Raised when the local Ollama service cannot be reached."""


class OllamaJsonParseError(Exception):
    """Raised when Ollama returns a response that cannot be parsed as JSON."""


def _extract_json_blob(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1).strip()
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        text = m.group(0)
    return text


def _repair_json_text(text: str) -> str:
    """Best-effort close for truncated model JSON (common with long 12-answer payloads)."""
    text = text.rstrip()
    if not text:
        return text
    # Drop trailing comma before we close brackets.
    while text.endswith(","):
        text = text[:-1].rstrip()
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    if open_brackets > 0 or open_braces > 0:
        text += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
    return text


def _loads_json_object(text: str) -> dict:
    blob = _extract_json_blob(text)
    if not blob:
        raise json.JSONDecodeError("empty response", text, 0)

    errors: list[json.JSONDecodeError] = []
    for candidate in (blob, _repair_json_text(blob)):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            errors.append(exc)

    # Walk backward to the last position that forms valid JSON.
    for end in range(len(blob), max(0, len(blob) - 4000), -1):
        if blob[end - 1] not in "}]":
            continue
        candidate = _repair_json_text(blob[:end])
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

    raise errors[0] if errors else json.JSONDecodeError("invalid JSON", blob, 0)


def _parse_json_response(text: str) -> dict:
    return _unwrap_llm_json(_loads_json_object(text))


def _unwrap_llm_json(data: object) -> dict:
    """Normalize gemma4-style nested JSON ({thought, output}) to the expected schema."""
    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")

    if "answers" in data or "claimed_company" in data or "content_type" in data:
        return data

    for key in ("output", "response", "result", "data", "thought", "thinking"):
        nested = data.get(key)
        if isinstance(nested, dict):
            return _unwrap_llm_json(nested)
        if isinstance(nested, str) and nested.strip():
            try:
                return _parse_json_response(nested)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

    return data


def _ollama_num_predict(prompt: str) -> int:
    if "questions BATCH" in prompt:
        return 1400
    if "Answer 12 fraud-detection questions" in prompt:
        return 2800
    if "Extract ONLY factual observations" in prompt:
        return 1000
    return 1500


def _expected_batch_answer_count(prompt: str) -> Optional[int]:
    m = re.search(r"questions BATCH (\d+)-(\d+)", prompt)
    if not m:
        return None
    return int(m.group(2)) - int(m.group(1)) + 1


def _ollama_response_is_usable(parsed: dict, prompt: str) -> bool:
    if "questions BATCH" in prompt or "Answer 12 fraud-detection questions" in prompt:
        answers = parsed.get("answers")
        if not isinstance(answers, list):
            return False
        expected = _expected_batch_answer_count(prompt) or 12
        # Small batches (e.g. Q13 alone) need only 1 answer; 6-question batches allow one miss.
        if expected <= 2:
            min_required = expected
        else:
            min_required = max(3, expected - 1)
        return len(answers) >= min_required
    if "Extract ONLY factual observations" in prompt:
        return isinstance(parsed.get("business_offering_summary"), str) or "claimed_company" in parsed
    return bool(parsed)


async def _call_claude(prompt: str) -> dict:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed — run: pip install anthropic")

    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2500,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json_response(msg.content[0].text)


def _model_is_available(model_name: str, available_models: list[str]) -> bool:
    if model_name in available_models:
        return True
    return any(m == model_name or m.startswith(f"{model_name}:") for m in available_models)


async def log_ollama_startup_status(timeout: float = 5.0) -> None:
    """Print a one-line Ollama health check at app startup (never raises)."""
    if not config.OLLAMA_BASE_URL:
        return

    base_url = config.OLLAMA_BASE_URL.rstrip("/")
    model = config.OLLAMA_MODEL

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            models = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception:
        print(
            f"⚠ Ollama nicht erreichbar unter {config.OLLAMA_BASE_URL} — "
            "läuft der Ollama-Dienst? AI Fraud Analysis wird als SKIPPED markiert"
        )
        return

    if _model_is_available(model, models):
        print(f"✓ Ollama erreichbar, Modell '{model}' verfügbar")
    else:
        print(
            f"⚠ Ollama erreichbar, aber Modell '{model}' nicht gefunden — "
            f"führe 'ollama pull {model}' aus"
        )


async def _call_ollama(prompt: str) -> dict:
    """Call Ollama via /api/chat with thinking disabled for reliable JSON on gemma4."""
    prompts = [prompt, prompt + _JSON_RETRY_SUFFIX]
    base_url = config.OLLAMA_BASE_URL.rstrip("/")
    num_predict = _ollama_num_predict(prompt)

    try:
        timeout = httpx.Timeout(300.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt, current_prompt in enumerate(prompts):
                try:
                    resp = await client.post(
                        f"{base_url}/api/chat",
                        json={
                            "model": config.OLLAMA_MODEL,
                            "think": False,
                            "messages": [{"role": "user", "content": current_prompt}],
                            "stream": False,
                            "format": "json",
                            "options": {"temperature": 0, "num_predict": num_predict},
                            "keep_alive": "15m",
                        },
                    )
                    resp.raise_for_status()
                    body = resp.json()
                    message = body.get("message") or {}
                    raw = (message.get("content") or "").strip()
                    if not raw and message.get("thinking"):
                        raw = str(message["thinking"]).strip()
                    parsed = _parse_json_response(raw)
                    if not _ollama_response_is_usable(parsed, current_prompt):
                        raise ValueError("incomplete JSON payload from model")
                    return parsed
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    if attempt < len(prompts) - 1:
                        continue
                    raise OllamaJsonParseError(
                        "KI-Antwort konnte nicht als JSON gelesen werden — "
                        f"prüfe ob Ollama läuft und Modell '{config.OLLAMA_MODEL}' verfügbar ist. "
                        "Alternativ in .env ein anderes Modell setzen (z.B. llama3.1:8b)"
                    ) from None
    except OllamaJsonParseError:
        raise
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise OllamaConnectionError(
                f"Ollama-Modell '{config.OLLAMA_MODEL}' nicht gefunden — "
                f"führe 'ollama pull {config.OLLAMA_MODEL}' aus"
            ) from e
        raise OllamaConnectionError(
            f"Ollama-Anfrage fehlgeschlagen (HTTP {e.response.status_code})"
        ) from e
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, ConnectionError, OSError):
        raise OllamaConnectionError(
            "Ollama nicht erreichbar — starte Ollama (App öffnen oder `ollama serve`)"
        )


async def _call_llm_backend(prompt: str, use_claude: bool) -> dict:
    if use_claude:
        return await _call_claude(prompt)
    return await _call_ollama(prompt)


def _build_facts_prompt(url: str, content: str, page_count: int, cross_context: str) -> str:
    return _FACTS_PROMPT.format(
        url=url,
        content=content,
        page_count=page_count,
        cross_context=cross_context,
    )


def _build_questions_prompt(
    url: str,
    content: str,
    facts: dict,
    cross_context: str,
) -> str:
    return _QUESTIONS_PROMPT.format(
        swiss_examples=_SWISS_EXAMPLES,
        fraud_examples=format_fraud_examples_for_prompt(limit=3),
        cross_context=cross_context,
        facts_json=json.dumps(facts, ensure_ascii=False, indent=2),
        content=content,
    )


def _build_questions_prompt_batch(
    url: str,
    content: str,
    facts: dict,
    cross_context: str,
    start_q: int,
    end_q: int,
    transaction: TransactionContext | None = None,
    extra_questions: dict[int, str] | None = None,
) -> str:
    """Smaller question batch — gemma4 often truncates a single 12-answer JSON payload."""
    extra_questions = extra_questions or {}
    tx_prefix = ""
    if transaction and transaction.is_present():
        tx_prefix = _build_transaction_context_block(transaction)

    def _question_text(q: int) -> str:
        return extra_questions.get(q) or _QUESTION_TEXTS[q]

    question_lines = "\n".join(
        f"{q}. {_question_text(q)}" for q in range(start_q, end_q + 1)
    )
    answer_lines = ",\n    ".join(
        f'{{"question": {q}, "answer": "yes|no|unclear", "reasoning": "<max 8 words>", "evidence_quote": ""}}'
        for q in range(start_q, end_q + 1)
    )
    content_type_field = (
        ',\n  "content_type": "<kurze Beschreibung, was die Seite anzubieten scheint>"'
        if end_q == _QUESTION_COUNT
        else ""
    )
    return f"""You are a Swiss financial fraud analyst specializing in facade and investment fraud targeting Swiss consumers.

{tx_prefix}Answer fraud-detection questions BATCH {start_q}-{end_q} using ONLY the extracted facts JSON and the content excerpts below. Do not invent facts. Answer each question independently.

{_SWISS_EXAMPLES}

{format_fraud_examples_for_prompt(limit=3)}

Cross-check context (deterministic results — do NOT contradict these):
{cross_context}

Extracted facts (Pass 1):
{json.dumps(facts, ensure_ascii=False, indent=2)}

Content excerpts:
---
{content}
---

Rules:
- For "yes": you MUST include evidence_quote — an exact substring (minimum 10 characters) copied verbatim from the content that proves the risk signal.
- If you cannot quote exact supporting text, answer "unclear" instead of "yes".
- For "no" or "unclear": evidence_quote must be empty string "".
- Return exactly {end_q - start_q + 1} answers for questions {start_q} through {end_q}.

Questions:
{question_lines}

Respond ONLY with JSON:
{{
  "answers": [
    {answer_lines}
  ]{content_type_field}
}}

JSON only, no other text."""


async def _run_questions_analysis(
    url: str,
    corpus: str,
    facts: dict,
    cross_context: str,
    use_claude: bool,
    transaction: TransactionContext | None = None,
) -> dict:
    """Two parallel 6-question batches; optional third batch for Q13 with transaction context."""
    extra_questions: dict[int, str] = {}
    batches: list[tuple[int, int]] = [(1, 6), (7, _QUESTION_COUNT)]
    if transaction and transaction.is_present():
        extra_questions[13] = _build_question_13_text(transaction)
        batches.append((13, 13))

    prompts = [
        _build_questions_prompt_batch(
            url, corpus, facts, cross_context, start, end,
            transaction=transaction,
            extra_questions=extra_questions,
        )
        for start, end in batches
    ]
    results = await asyncio.gather(*[
        _call_llm_backend(prompt, use_claude) for prompt in prompts
    ])

    merged: dict[str, Any] = {"answers": [], "content_type": ""}
    for batch in results:
        if not isinstance(batch, dict):
            continue
        answers = batch.get("answers")
        if isinstance(answers, list):
            merged["answers"].extend(answers)
        ct = batch.get("content_type")
        if isinstance(ct, str) and ct.strip():
            merged["content_type"] = ct.strip()
    return merged


class LLMContentCheck(BaseCheck):
    name = "llm_content"
    display_name = "AI Fraud Analysis"
    max_score = _MAX_SCORE
    tier = 2

    async def run(self, domain: str, url: str = "", **kwargs) -> CheckResult:
        use_claude = bool(config.ANTHROPIC_API_KEY)
        use_ollama = bool(config.OLLAMA_BASE_URL)

        if not use_claude and not use_ollama:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.SKIPPED,
                score=0,
                max_score=self.max_score,
                summary="No LLM configured — set ANTHROPIC_API_KEY or OLLAMA_BASE_URL in .env",
                details={
                    "skipped": True,
                    "note": "Set ANTHROPIC_API_KEY (Claude) or OLLAMA_BASE_URL + OLLAMA_MODEL (local)",
                },
            )

        target_url = url if url.startswith("http") else f"https://{domain}"
        check_context: dict = kwargs.get("check_context") or {}
        transaction: TransactionContext | None = kwargs.get("transaction_context")
        include_q13 = transaction is not None and transaction.is_present()

        try:
            gathered = await gather_llm_content(target_url, timeout_ms=15000)
            if not gathered.get("success"):
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.ERROR,
                    score=0,
                    max_score=self.max_score,
                    summary=gathered.get("error") or "Failed to load page for analysis",
                    details={},
                )

            html = gathered["html"]
            status_code = gathered.get("status_code") if gathered.get("status_code") is not None else 0
            content_diagnostics = _detect_blocking(html, status_code)
            corpus = gathered.get("corpus") or ""
            sections = gathered.get("sections") or []

            if len(corpus) < 80:
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.NA,
                    score=0,
                    max_score=self.max_score,
                    summary="Page returned too little readable content to analyze",
                    details={
                        "content_diagnostics": content_diagnostics,
                        "pages_analyzed": len(sections),
                    },
                )

            cross_context = format_context_for_prompt(check_context)
            facts_prompt = _build_facts_prompt(
                target_url,
                corpus,
                len(sections),
                cross_context,
            )
            facts = await _call_llm_backend(facts_prompt, use_claude)

            analysis = await _run_questions_analysis(
                target_url,
                corpus,
                facts if isinstance(facts, dict) else {},
                cross_context,
                use_claude,
                transaction=transaction,
            )
            backend = "claude-haiku" if use_claude else f"ollama/{config.OLLAMA_MODEL}"

            answers = _normalize_answers(analysis.get("answers"), include_q13=include_q13)
            answers = _apply_evidence_validation(answers, corpus)
            answers, override_notes = _apply_deterministic_overrides(
                answers, check_context, sections,
            )

            content_type = (
                analysis.get("content_type", "")
                if isinstance(analysis.get("content_type"), str)
                else ""
            )
            if not content_type and isinstance(facts, dict):
                content_type = facts.get("business_offering_summary") or ""

            result = _calculate_score_from_answers(answers, corpus=corpus)
            score = result["score"]
            confidence = result["confidence"]
            fraud_category = result["fraud_category"]
            status = result["status"]

            if content_diagnostics["likely_blocked"]:
                confidence = "low"
            elif confidence == "low" and result["validated_yes_count"] >= 1:
                confidence = "medium"

            question_texts = dict(_QUESTION_TEXTS)
            if include_q13 and transaction:
                question_texts[13] = _build_question_13_text(transaction)

            details: dict[str, Any] = {
                "answers": answers,
                "question_texts": question_texts,
                "questions_asked": list(range(1, (13 if include_q13 else 12) + 1)),
                "score_breakdown": _build_score_breakdown(answers),
                "content_type": content_type,
                "confidence": confidence,
                "score_weight": 1.0 if confidence == "high" else 0.5,
                "fraud_category": fraud_category,
                "yes_count": result["yes_count"],
                "validated_yes_count": result["validated_yes_count"],
                "unclear_count": result["unclear_count"],
                "backend": backend,
                "content_diagnostics": content_diagnostics,
                "pages_analyzed": len(sections),
                "page_sections": [
                    {"label": s.get("label"), "url": s.get("url")}
                    for s in sections
                ],
                "extracted_facts": facts if isinstance(facts, dict) else {},
                "cross_check_context": check_context,
                "cross_check_overrides": override_notes,
                "analysis_passes": 3,
                "transaction_context": transaction.model_dump() if include_q13 and transaction else None,
            }

            if confidence == "low":
                return CheckResult(
                    name=self.name,
                    display_name=self.display_name,
                    status=CheckStatus.SKIPPED,
                    score=0,
                    max_score=self.max_score,
                    summary=(
                        "AI-Analyse übersprungen — zu viele unklare Antworten oder "
                        "unzureichender Seiteninhalt (siehe Diagnose)"
                    ),
                    details={
                        **details,
                        "skipped_reason": "low_confidence_or_blocked_content",
                    },
                )

            summary = _build_summary(
                score,
                fraud_category,
                content_type,
                result["validated_yes_count"],
            )
            if confidence == "medium":
                summary = f"[Halbe Gewichtung] {summary}"
            if content_diagnostics["likely_blocked"]:
                summary = _BLOCKED_SUMMARY

            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=status,
                score=score,
                max_score=self.max_score,
                summary=summary,
                details=details,
            )

        except OllamaConnectionError as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=str(e),
                details={},
            )
        except OllamaJsonParseError as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=str(e),
                details={"retryable": False, "error_kind": "json_parse"},
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                display_name=self.display_name,
                status=CheckStatus.ERROR,
                score=0,
                max_score=self.max_score,
                summary=f"LLM analysis failed: {str(e)[:120]}",
                details={},
            )
