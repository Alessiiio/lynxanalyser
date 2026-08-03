const FRAUD_CONFIRM_CATEGORIES = [
  ["investment_fraud", "Anlagebetrug"],
  ["phishing_impersonation", "Phishing/Identitätsmissbrauch"],
  ["support_scam", "Support-/Tech-Betrug"],
  ["booking_scam", "Vorschussbetrug Buchung"],
  ["marketplace_scam", "Marktplatz-Betrug"],
  ["fake_shop", "Fake-Shop"],
  ["general_suspicious", "Mehrere Warnsignale"],
];

const CHECK_ORDER = [
  "whois", "ssl", "dns", "hosting", "wayback", "crt",
  "hsts", "contact", "trustpilot", "google_reviews", "social_media", "virustotal", "safebrowsing", "urlscan", "zefix", "finma", "iscan", "llm_content",
];

const CHECK_TIER_FALLBACK = {
  whois: 2, ssl: 3, dns: 3, hosting: 3, wayback: 3, crt: 3, hsts: 3,
  contact: 2, trustpilot: 2, google_reviews: 2, social_media: 2,
  virustotal: 1, safebrowsing: 1, urlscan: 3, zefix: 2, finma: 1, iscan: 1, llm_content: 2,
};

const CHECK_META = {
  whois:        { display: "Domain-Alter",           icon: "🕰️" },
  ssl:          { display: "SSL-Zertifikat",         icon: "🔒" },
  dns:          { display: "DNS-Einträge",           icon: "📡" },
  hosting:      { display: "Hosting-Standort",       icon: "🌍" },
  wayback:      { display: "Web-Archiv",             icon: "📚" },
  crt:          { display: "Zertifikatshistorie",    icon: "📜" },
  hsts:         { display: "HSTS-Sicherheit",        icon: "🛡️" },
  contact:      { display: "Kontaktangaben",         icon: "📞" },
  trustpilot:   { display: "Trustpilot",             icon: "⭐" },
  google_reviews: { display: "Google Bewertungen",   icon: "📍" },
  social_media: { display: "Social Media",           icon: "📱" },
  virustotal:   { display: "VirusTotal",             icon: "🦠" },
  safebrowsing: { display: "Google Safe Browsing",   icon: "🔍" },
  urlscan:      { display: "URLScan",                icon: "🌐" },
  zefix:        { display: "Handelsregister (Zefix)", icon: "🇨🇭" },
  finma:        { display: "FINMA-Warnliste",        icon: "🏛️" },
  iscan:        { display: "I-SCAN (IOSCO)",           icon: "🌐" },
  llm_content:  { display: "KI-Betrugsanalyse",      icon: "🤖" },
};

const REPUTATION_CHECKS = new Set(["trustpilot", "google_reviews"]);

const TIER_WEIGHTS = { 1: 0.55, 2: 0.30, 3: 0.15 };
const SCORE_EXCLUDED = new Set(["skipped", "na", "error"]);

const LLM_QUESTION_LABELS = {
  1: "Unrealistische Renditeversprechen",
  2: "Zeitdruck / Verknappung",
  3: "Identitätsmissbrauch Institution",
  4: "Vages Geschäftsmodell",
  5: "Fehlende Kontaktangaben",
  6: "Unbelegte Testimonials",
  7: "Verdächtige Zahlungswege",
  8: "Sprachliche Auffälligkeiten",
  9: "Reisserischer Ton",
  10: "Fehlender Rechtsrahmen",
  11: "Panik-/Alarm-Warnung (Tech-Support)",
  12: "Anruf-/Fernzugriff-Aufforderung",
  13: "Betrag/Zweck passt nicht zum Angebot",
};

const LLM_QUESTION_PENALTIES = {
  1: 4, 2: 1, 3: 5, 4: 2, 5: 2, 6: 1, 7: 3, 8: 1, 9: 1, 10: 2, 11: 3, 12: 5, 13: 6,
};

function getLlmQuestionLabel(q, questionTexts) {
  return LLM_QUESTION_LABELS[q] || questionTexts?.[q] || `Frage ${q}`;
}

function isLegacyLlmDetails(d) {
  return !Array.isArray(d?.answers) && (
    d?.fraud_probability != null || (d?.indicators && typeof d.indicators === "object")
  );
}

const SCORE_STAMP_CLASSES = ["stamp-green", "stamp-yellow", "stamp-orange", "stamp-red", "stamp-gray"];

const COLOR_MAP = {
  green:  { score: "color-green",  badge: "badge-green",  bar: "bar-green" },
  yellow: { score: "color-yellow", badge: "badge-yellow", bar: "bar-yellow" },
  orange: { score: "color-orange", badge: "badge-orange", bar: "bar-orange" },
  red:    { score: "color-red",    badge: "badge-red",    bar: "bar-red" },
  gray:   { score: "color-gray",   badge: "badge-gray",   bar: "bar-red" },
};

function simpleBadgeText(result) {
  if (result.status === "skipped" || result.status === "na") return "—";
  if (result.status === "passed") return "OK";
  if (result.status === "warning") return "Warnung";
  if (result.status === "failed" || result.status === "error") return "Risiko";
  return "—";
}

function displayBadgeText(result) {
  if (isExpertMode()) {
    if (result.status === "skipped" || result.status === "na") return result.status.toUpperCase();
    return `+${result.score}/${result.max_score}`;
  }
  return simpleBadgeText(result);
}

window.onExpertModeChange = function () {
  const hint = document.getElementById("simpleModeHint");
  if (hint) hint.classList.toggle("hidden", isExpertMode());
  if (lastReport?.checks) {
    lastReport.checks.forEach(updateCheckCard);
  } else {
    document.querySelectorAll(".check-card[id^='card-']").forEach((card) => {
      const name = card.id.replace("card-", "");
      if (completedChecks.find((c) => c.name === name)) {
        updateCheckCard(completedChecks.find((c) => c.name === name));
      }
    });
  }
};

document.addEventListener("DOMContentLoaded", () => {
  const hint = document.getElementById("simpleModeHint");
  if (hint) hint.classList.toggle("hidden", isExpertMode());
});

let lastReport = null;
let currentSource = null;
let completedChecks = [];
let allChecksComplete = false;
let lastTransactionContext = null;

function getTransactionContextFromForm() {
  const rawAmount = document.getElementById("transactionAmountInput")?.value?.trim();
  const currency = document.getElementById("transactionCurrencyInput")?.value || "CHF";
  const purpose = document.getElementById("transactionPurposeInput")?.value?.trim() || "";

  if (!rawAmount) {
    return null;
  }
  const amount = Number(rawAmount);
  if (!Number.isFinite(amount) || amount <= 0) {
    return null;
  }
  return {
    transaction_amount: amount,
    transaction_currency: currency,
    transaction_purpose: purpose || null,
  };
}

function formatTransactionAmountDisplay(amount, currency) {
  const formatted = Number(amount).toLocaleString("de-CH", { maximumFractionDigits: 2 });
  return `${currency} ${formatted}`;
}

function appendTransactionParams(params, tx) {
  if (!tx) return;
  params.set("transaction_amount", String(tx.transaction_amount));
  params.set("transaction_currency", tx.transaction_currency || "CHF");
  if (tx.transaction_purpose) {
    params.set("transaction_purpose", tx.transaction_purpose);
  }
}

document.getElementById("checkForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const url     = document.getElementById("urlInput").value.trim();
  const company = document.getElementById("companyInput").value.trim();
  if (!url) return;
  if (currentSource) { currentSource.close(); currentSource = null; }
  lastTransactionContext = getTransactionContextFromForm();
  updateShareableUrl(url, company);
  startCheck(url, company, lastTransactionContext);
});

function updateShareableUrl(url, company) {
  const params = new URLSearchParams({ url });
  if (company) params.set("company", company);
  history.replaceState(null, "", `?${params.toString()}`);
}

function loadFromQueryParams() {
  const params = new URLSearchParams(window.location.search);
  const url = params.get("url");
  const company = params.get("company") || "";
  if (url) {
    document.getElementById("urlInput").value = url;
    document.getElementById("companyInput").value = company;
    if (params.get("autostart") === "1") {
      startCheck(url, company);
    }
  }
}

loadFromQueryParams();

function startCheck(url, company, transactionContext = null) {
  lastTransactionContext = transactionContext || null;
  lastReport = null;
  completedChecks = [];
  allChecksComplete = false;

  setProgress(5);
  setSubmitLoading(true);

  const domain = extractDomain(url);
  showScanLoader(domain);

  document.getElementById("results").classList.remove("hidden");
  document.getElementById("checksGrid").innerHTML = "";
  document.getElementById("liveResultsHeader")?.classList.add("hidden");
  document.body.classList.remove("scan-has-results");
  document.getElementById("criticalFlags").classList.add("hidden");
  document.getElementById("copyBtn").classList.add("hidden");
  document.getElementById("pdfBtn").classList.add("hidden");

  initLiveScoreCard(domain);

  resetResultsForScan();

  const params = new URLSearchParams({ url });
  if (company) params.set("company", company);
  appendTransactionParams(params, transactionContext);

  const source = new EventSource(`/api/stream?${params}`);
  currentSource = source;
  let completed = 0;

  source.onmessage = (evt) => {
    const data = JSON.parse(evt.data);

    if (data.type === "goldlist") {
      document.getElementById("scanLoaderGoldlist")?.classList.remove("hidden");
      setScanLoaderStatus("Goldlist — Schnellprüfung läuft…");
    } else if (data.type === "blocklist") {
      document.getElementById("scanLoaderBlocklist")?.classList.remove("hidden");
      setScanLoaderStatus("Blocklist — bekannter Betrug erkannt");
    } else if (data.type === "thought") {
      appendThought(data.text, true);
    } else if (data.type === "retry") {
      insertCheckCardInOrder(data.name);
      updateCheckCardRetrying(data.name, data.attempt, data.max_attempts);
      const label = CHECK_META[data.name]?.display || data.display_name || data.name;
      setScanLoaderStatus(`${label} — erneuter Versuch…`);
    } else if (data.type === "check") {
      completed++;
      completedChecks.push(data.result);
      revealCompletedCheck(data.result);
      updateLiveScorePreview(completedChecks, completed, CHECK_ORDER.length);
      updateScanLoader(completed, CHECK_ORDER.length);
      setProgress(5 + Math.round((completed / CHECK_ORDER.length) * 80));
      appendThought(thoughtForResult(data.result));
      const meta = CHECK_META[data.result.name];
      if (meta) setScanLoaderStatus(`${meta.display} abgeschlossen`);
    } else if (data.type === "report") {
      lastReport = data.report;
      if (data.report.cached) {
        appendThought("Ergebnis aus Cache geladen (kürzlich geprüft)");
      }
      if (completed === 0 && data.report.checks?.length) {
        const ordered = CHECK_ORDER
          .map((name) => data.report.checks.find((c) => c.name === name))
          .filter(Boolean);
        for (const check of ordered) {
          revealCompletedCheck(check);
        }
        completed = ordered.length;
        updateScanLoader(completed, CHECK_ORDER.length);
        for (const check of ordered) {
          appendThought(thoughtForResult(check));
        }
      }
      appendThought(`Bewertung: ${translateVerdict(data.report.verdict)} (${data.report.total_score}/100)`);
      finalizeScore(data.report);
      setProgress(100);
      setSubmitLoading(false);
      hideScanLoader();
      source.close();
      currentSource = null;
      setTimeout(() => setProgress(0), 800);
    } else if (data.type === "error") {
      setSubmitLoading(false);
      hideScanLoader();
      setProgress(0);
      source.close();
      currentSource = null;
    }
  };

  source.onerror = () => {
    setSubmitLoading(false);
    hideScanLoader();
    setProgress(0);
    source.close();
    currentSource = null;
  };
}

function showScanLoader(domain) {
  document.getElementById("searchCard")?.classList.add("hidden");
  const loader = document.getElementById("scanLoader");
  loader?.classList.remove("hidden");
  document.body.classList.add("scanning");
  document.getElementById("scanLoaderDomain").textContent = domain;
  setScanLoaderStatus("Scan wird gestartet…");
  document.getElementById("scanLoaderGoldlist")?.classList.add("hidden");
  updateScanLoader(0, CHECK_ORDER.length);
  clearThoughtLog();
  appendThought("Verbindung zum Analyse-Server hergestellt");
  appendThought(`Ziel-Domain: ${domain}`, true);
}

function hideScanLoader() {
  document.getElementById("searchCard")?.classList.remove("hidden");
  document.getElementById("scanLoader")?.classList.add("hidden");
  document.body.classList.remove("scanning");
  document.getElementById("liveResultsHeader")?.classList.add("hidden");
}

function setScanLoaderStatus(text) {
  const el = document.getElementById("scanLoaderStatus");
  if (el) el.textContent = text;
}

function updateScanLoader(completed, total) {
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  const fill = document.getElementById("scanLoaderFill");
  if (fill) fill.style.width = `${pct}%`;
  const progress = document.getElementById("scanLoaderProgress");
  if (progress) progress.textContent = `${completed} von ${total} Prüfungen`;
}

function clearThoughtLog() {
  const container = document.getElementById("scanThoughtLines");
  if (container) container.innerHTML = "";
}

function appendThought(text, active = false) {
  const container = document.getElementById("scanThoughtLines");
  if (!container || !text) return;

  container.querySelectorAll(".scan-thought-line.active").forEach((el) => {
    el.classList.remove("active");
    el.classList.add("done");
  });

  const line = document.createElement("p");
  line.className = `scan-thought-line${active ? " active" : " done"}`;
  line.textContent = text;
  container.appendChild(line);

  const log = document.getElementById("scanThoughtLog");
  if (log) log.scrollTop = log.scrollHeight;
}

function thoughtForResult(result) {
  const meta = CHECK_META[result.name];
  const label = meta?.display || result.display_name;
  if (result.details?.goldlist_skip) {
    return `– ${label} übersprungen (Goldlist)`;
  }
  if (result.details?.blocklist_skip) {
    return `– ${label} übersprungen (Blocklist)`;
  }
  const sym = { passed: "✓", warning: "!", failed: "✗", error: "✗", skipped: "–", na: "–" }[result.status] || "·";
  const summary = result.summary
    .replace(/\[Halbe Gewichtung\]\s*/g, "")
    .replace(/\(\d+\/\d+ pts\)/g, "")
    .trim()
    .slice(0, 140);
  return `${sym} ${label}: ${summary}`;
}

function resetResultsForScan() {
  const scoreCard = document.getElementById("scoreCard");
  if (!document.body.classList.contains("scanning")) {
    scoreCard?.classList.add("hidden");
  }

  document.getElementById("previousScanBox")?.classList.add("hidden");
  document.getElementById("previousScanBox").innerHTML = "";

  const goldBox = document.getElementById("goldlistBox");
  if (goldBox) {
    goldBox.classList.add("hidden");
    goldBox.innerHTML = "";
  }

  const blockBox = document.getElementById("blocklistBox");
  if (blockBox) {
    blockBox.classList.add("hidden");
    blockBox.innerHTML = "";
  }

  const fraudBox = document.getElementById("fraudConfirmBox");
  if (fraudBox) {
    fraudBox.classList.add("hidden");
    fraudBox.innerHTML = "";
  }

  document.getElementById("scanLoaderBlocklist")?.classList.add("hidden");

  document.getElementById("criticalFlags")?.classList.add("hidden");
  document.getElementById("criticalFlagsList").innerHTML = "";
  document.getElementById("warningFlags")?.classList.add("hidden");
  document.getElementById("warningFlagsList").innerHTML = "";
  document.getElementById("transactionContextBox")?.classList.add("hidden");
  document.getElementById("transactionContextBox").innerHTML = "";
  document.getElementById("liveResultsHeader")?.classList.add("hidden");
  document.getElementById("simpleModeHint")?.classList.toggle("hidden", isExpertMode());
}

// ── Score card ────────────────────────────────────────────────

function getCheckTier(name, result) {
  if (result?.tier) return result.tier;
  return CHECK_TIER_FALLBACK[name] || 2;
}

function buildTierEyebrow(tier) {
  if (!isExpertMode()) return "";
  return tier === 1 ? '<div class="check-tier-eyebrow">Primärsignal</div>' : "";
}

function setScoreCardStamp(color) {
  const el = document.getElementById("scoreCard");
  SCORE_STAMP_CLASSES.forEach((cls) => el.classList.remove(cls));
  el.classList.add(`stamp-${color}`);
}

function initScoreCard(domain) {
  const scoreCard = document.getElementById("scoreCard");
  scoreCard.classList.remove("hidden");
  setScoreCardStamp("gray");

  document.getElementById("scoreNumber").textContent = "–";
  document.getElementById("scoreNumber").className = "score-number color-gray";
  document.getElementById("scoreVerdict").textContent = "Analysiere…";
  document.getElementById("scoreVerdict").className = "score-verdict color-gray";
  document.getElementById("scoreDomain").textContent = domain;
  document.getElementById("scoreProgress").classList.add("hidden");
  document.getElementById("scoreProgress").classList.remove("score-progress-done");

  const bar = document.getElementById("scoreBar");
  bar.className = "score-bar bar-gray";
  bar.style.width = "0%";
}

function effectiveCheckPoints(check) {
  if (SCORE_EXCLUDED.has(check.status)) return [0, 0];
  let earned = check.score ?? 0;
  const max = check.max_score ?? 0;
  if (
    check.name === "llm_content"
    && !check.details?.user_dismissed
    && check.details?.confidence === "medium"
  ) {
    earned = Math.floor(earned / 2);
  }
  return [earned, max];
}

function detectPartialCriticalFlags(checks) {
  const flags = [];
  for (const check of checks) {
    if (check.name === "safebrowsing" && check.details?.threats?.length) {
      flags.push("Google Safe Browsing: Bedrohung erkannt");
    }
    if (check.name === "virustotal" && (check.details?.malicious || 0) > 5) {
      flags.push(`VirusTotal: ${check.details.malicious} Engines`);
    }
    if (check.name === "finma" && check.details?.listed) {
      flags.push("FINMA-Warnliste");
    }
    if (check.name === "iscan" && check.details?.listed) {
      flags.push("I-SCAN-Warnliste");
    }
    if (check.name === "whois") {
      const age = check.details?.age_days;
      if (age != null && age > 0 && age < 30) {
        flags.push("Domain jünger als 30 Tage");
      }
    }
  }
  return flags;
}

function computePartialScore(checks) {
  const tiers = {
    1: { earned: 0, max: 0 },
    2: { earned: 0, max: 0 },
    3: { earned: 0, max: 0 },
  };

  for (const check of checks) {
    const tier = getCheckTier(check.name, check);
    const [earned, max] = effectiveCheckPoints(check);
    if (max <= 0) continue;
    tiers[tier].earned += earned;
    tiers[tier].max += max;
  }

  const active = [1, 2, 3].filter((t) => tiers[t].max > 0);
  if (!active.length) {
    return { score: null, verdict: "Zwischenstand…", color: "gray", evaluated: 0 };
  }

  const weightSum = active.reduce((sum, t) => sum + TIER_WEIGHTS[t], 0);
  let normalized = 0;
  for (const t of active) {
    const subscore = Math.round((tiers[t].earned / tiers[t].max) * 100);
    normalized += subscore * (TIER_WEIGHTS[t] / weightSum);
  }
  normalized = Math.round(normalized);

  const critical = detectPartialCriticalFlags(checks);
  let verdict;
  let color;
  if (critical.length) {
    verdict = "Kritisches Risiko";
    color = "red";
  } else if (normalized >= 75) {
    verdict = "Wahrscheinlich legitim";
    color = "green";
  } else if (normalized >= 50) {
    verdict = "Vorsicht";
    color = "yellow";
  } else if (normalized >= 25) {
    verdict = "Hohes Risiko";
    color = "orange";
  } else {
    verdict = "Wahrscheinlich betrügerisch";
    color = "red";
  }

  const evaluated = checks.filter((c) => !SCORE_EXCLUDED.has(c.status)).length;
  return { score: normalized, verdict, color, evaluated, critical };
}

function initLiveScoreCard(domain) {
  const scoreCard = document.getElementById("scoreCard");
  scoreCard.classList.remove("hidden");
  scoreCard.classList.add("score-card-live");

  setScoreCardStamp("gray");
  document.getElementById("scoreNumber").textContent = "–";
  document.getElementById("scoreNumber").className = "score-number color-gray";
  document.getElementById("scoreVerdict").textContent = "Zwischenstand…";
  document.getElementById("scoreVerdict").className = "score-verdict color-gray";
  document.getElementById("scoreDomain").textContent = domain;

  const progress = document.getElementById("scoreProgress");
  progress.textContent = `0 von ${CHECK_ORDER.length} Prüfungen ausgewertet`;
  progress.classList.remove("hidden", "score-progress-done");

  const bar = document.getElementById("scoreBar");
  bar.className = "score-bar bar-gray";
  bar.style.width = "0%";

  document.getElementById("criticalFlags")?.classList.add("hidden");
  document.getElementById("criticalFlagsList").innerHTML = "";
}

function updateLiveScorePreview(checks, completed, total) {
  if (allChecksComplete) return;

  const preview = computePartialScore(checks);
  const scoreCard = document.getElementById("scoreCard");
  scoreCard.classList.add("score-card-live");

  const progress = document.getElementById("scoreProgress");
  progress.textContent = preview.score != null
    ? `${completed} von ${total} Prüfungen · Zwischenstand (${preview.evaluated} gewertet)`
    : `${completed} von ${total} Prüfungen ausgewertet`;
  progress.classList.remove("hidden");

  if (preview.score == null) return;

  setScoreCardStamp(preview.color);
  const colors = COLOR_MAP[preview.color] || COLOR_MAP.gray;

  const numEl = document.getElementById("scoreNumber");
  numEl.className = `score-number ${colors.score}`;
  animateNumber(numEl, preview.score);

  const verdictEl = document.getElementById("scoreVerdict");
  verdictEl.textContent = translateVerdict(preview.verdict);
  verdictEl.className = `score-verdict ${colors.score}`;

  const bar = document.getElementById("scoreBar");
  bar.className = `score-bar ${colors.bar}`;
  bar.style.width = `${preview.score}%`;

  if (preview.critical?.length) {
    document.getElementById("criticalFlagsList").innerHTML =
      preview.critical.map((f) => `<li>${escHtml(f)}</li>`).join("");
    document.getElementById("criticalFlags").classList.remove("hidden");
  }
}

function finalizeScore(report) {
  allChecksComplete = true;
  const scoreCard = document.getElementById("scoreCard");
  scoreCard.classList.remove("hidden", "score-card-live");

  document.getElementById("scoreDomain").textContent = report.domain;

  const color = report.verdict_color;
  setScoreCardStamp(color);
  const colors = COLOR_MAP[color] || COLOR_MAP.gray;

  const numEl = document.getElementById("scoreNumber");
  numEl.className = `score-number ${colors.score}`;
  animateNumber(numEl, report.total_score);

  const verdictEl = document.getElementById("scoreVerdict");
  verdictEl.textContent = translateVerdict(report.verdict);
  verdictEl.className = `score-verdict ${colors.score}`;

  document.getElementById("scoreProgress").textContent = report.cached
    ? "Aus Cache (kürzlich geprüft)"
    : "Analyse abgeschlossen";
  document.getElementById("scoreProgress").classList.remove("hidden");
  document.getElementById("scoreProgress").classList.add("score-progress-done");

  renderGoldlistBox(report);
  renderBlocklistBox(report);
  renderFraudConfirmBox(report);
  renderTransactionContextBox(report, lastTransactionContext);

  const bar = document.getElementById("scoreBar");
  bar.className = `score-bar ${colors.bar}`;
  bar.style.width = `${report.total_score}%`;

  if (report.critical_flags?.length) {
    document.getElementById("criticalFlagsList").innerHTML =
      report.critical_flags.map(f => `<li>${escHtml(f)}</li>`).join("");
    document.getElementById("criticalFlags").classList.remove("hidden");
  } else {
    document.getElementById("criticalFlags").classList.add("hidden");
    document.getElementById("criticalFlagsList").innerHTML = "";
  }

  if (report.warning_flags?.length) {
    document.getElementById("warningFlagsList").innerHTML =
      report.warning_flags.map(f => `<li>${escHtml(f)}</li>`).join("");
    document.getElementById("warningFlags").classList.remove("hidden");
  } else {
    document.getElementById("warningFlags").classList.add("hidden");
    document.getElementById("warningFlagsList").innerHTML = "";
  }

  for (const check of report.checks) {
    updateCheckCard(check);
  }

  renderPreviousScan(report.previous_scan);
  document.getElementById("copyBtn").classList.remove("hidden");
  document.getElementById("pdfBtn").classList.remove("hidden");
}

function renderTransactionContextBox(report, tx) {
  const box = document.getElementById("transactionContextBox");
  if (!box) return;

  if (!tx?.transaction_amount) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }

  const amountLabel = formatTransactionAmountDisplay(
    tx.transaction_amount,
    tx.transaction_currency || "CHF",
  );
  const purpose = tx.transaction_purpose
    ? `Verwendungszweck: «${tx.transaction_purpose}»`
    : "";

  const llm = report?.checks?.find((c) => c.name === "llm_content");
  const q13Hit = Array.isArray(llm?.details?.answers)
    && llm.details.answers.some((a) => a.question === 13 && a.answer === "yes");

  let html = `<div class="transaction-context-box-inner">
    <span class="transaction-context-box-label">Analysiert mit Transaktionskontext:</span>
    <span class="transaction-context-box-value">${escHtml(amountLabel)}</span>`;
  if (purpose) {
    html += `<span class="transaction-context-box-purpose">${escHtml(purpose)}</span>`;
  }
  if (q13Hit) {
    html += `<div class="transaction-context-box-alert">⚠ KI: Betrag/Zweck nicht konsistent mit Webseitenangebot</div>`;
  }
  html += `</div>`;

  box.innerHTML = html;
  box.classList.remove("hidden");
}

function renderGoldlistBox(report) {
  const goldBox = document.getElementById("goldlistBox");
  if (!goldBox) return;

  if (report.goldlist_match) {
    goldBox.innerHTML = `<span>✓ Bekannte legitime Domain (interne Goldlist) — Schnellprüfung durchgeführt</span>`;
    goldBox.classList.remove("hidden");
    return;
  }

  const domain = escHtml(report.domain);
  goldBox.innerHTML = `
    <span>Nicht auf der Goldlist.</span>
    <button type="button" class="btn-goldlist-add" data-domain="${domain}">Zur Goldlist hinzufügen</button>
  `;
  goldBox.classList.remove("hidden");
  goldBox.querySelector(".btn-goldlist-add")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const resp = await fetch("/api/goldlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: report.domain }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      goldBox.innerHTML = `<span>✓ «${domain}» zur Goldlist hinzugefügt</span>`;
    } catch (err) {
      btn.disabled = false;
      alert(`Goldlist: ${err.message}`);
    }
  });
}

function renderBlocklistBox(report) {
  const box = document.getElementById("blocklistBox");
  if (!box) return;

  if (!report.blocklist_match) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }

  const flag = (report.critical_flags || []).find((f) => f.includes("Interne Blocklist"));
  let categoryLabel = null;
  if (flag) {
    const m = flag.match(/\(([^)]+)\)/);
    const raw = m?.[1]?.split(" — ")[0];
    if (raw) {
      categoryLabel = FRAUD_CONFIRM_CATEGORIES.find(([k]) => k === raw)?.[1] || raw;
    }
  }
  if (!categoryLabel) {
    const llm = report.checks?.find((c) => c.name === "llm_content");
    const category = llm?.details?.fraud_category;
    if (category) {
      categoryLabel = FRAUD_CONFIRM_CATEGORIES.find(([k]) => k === category)?.[1] || category;
    }
  }

  box.innerHTML = `
    <span class="blocklist-badge">⛔ Interne Blocklist</span>
    <span>Diese Domain wurde als Betrug bestätigt — alle Prüfungen wurden übersprungen.</span>
    ${categoryLabel ? `<span class="blocklist-category">Kategorie: ${escHtml(categoryLabel)}</span>` : ""}
    <a href="/blocklist" class="blocklist-link">Blocklist verwalten</a>
  `;
  box.classList.remove("hidden");
}

function renderFraudConfirmBox(report) {
  const box = document.getElementById("fraudConfirmBox");
  if (!box) return;

  if (report.blocklist_match || report.goldlist_match) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }

  const llm = report.checks?.find((c) => c.name === "llm_content");
  const defaultCategory = llm?.details?.fraud_category || "general_suspicious";
  const options = FRAUD_CONFIRM_CATEGORIES.map(([value, label]) =>
    `<option value="${escHtml(value)}"${value === defaultCategory ? " selected" : ""}>${escHtml(label)}</option>`
  ).join("");

  box.innerHTML = `
    <div class="fraud-confirm-header">
      <span class="fraud-confirm-title">Als Betrug melden</span>
      <span class="fraud-confirm-hint">Domain wird auf die interne Blocklist gesetzt und fliesst in die KI-Kalibrierung ein.</span>
    </div>
    <div class="fraud-confirm-form">
      <label class="fraud-confirm-label" for="fraudCategorySelect">Betrugskategorie</label>
      <select id="fraudCategorySelect" class="fraud-confirm-select">${options}</select>
      <label class="fraud-confirm-label" for="fraudConfirmComment">Kommentar (optional)</label>
      <textarea id="fraudConfirmComment" class="fraud-confirm-textarea" rows="2" placeholder="z.B. Vishing über angeblichen Microsoft-Support"></textarea>
      <button type="button" class="btn-fraud-confirm" onclick="submitFraudConfirm()">Als Betrug bestätigen</button>
    </div>
  `;
  box.classList.remove("hidden");
}

window.submitFraudConfirm = async function () {
  if (!lastReport) return;
  if (!confirm(`«${lastReport.domain}» wirklich als Betrug bestätigen und auf die Blocklist setzen?`)) return;

  const category = document.getElementById("fraudCategorySelect")?.value || "general_suspicious";
  const comment = document.getElementById("fraudConfirmComment")?.value?.trim() || "";
  const btn = document.querySelector(".btn-fraud-confirm");
  if (btn) btn.disabled = true;

  try {
    const resp = await fetch("/api/fraud-confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain: lastReport.domain,
        url: lastReport.url,
        fraud_category: category,
        feedback_text: comment,
        checks: lastReport.checks || [],
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);

    const box = document.getElementById("fraudConfirmBox");
    if (box) {
      box.innerHTML = `<span class="fraud-confirm-done">✓ «${escHtml(lastReport.domain)}» auf Blocklist gesetzt — erneuter Scan zeigt sofort Kritisches Risiko</span>`;
    }
    lastReport = { ...lastReport, blocklist_match: true };
    renderBlocklistBox(lastReport);
  } catch (err) {
    if (btn) btn.disabled = false;
    alert(`Blocklist: ${err.message}`);
  }
};

function renderPreviousScan(previous) {
  const box = document.getElementById("previousScanBox");
  if (!previous) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }

  const dateLabel = formatDateTimeDisplay(previous.previous_checked_at);
  const diff = previous.score_diff;
  let boxClass = "previous-scan-neutral";
  let diffHtml = "<span>Score im Wesentlichen unverändert</span>";

  if (diff < -2) {
    boxClass = "previous-scan-worse";
    diffHtml = `<span>↓ Score hat sich um ${Math.abs(diff)} Punkte verschlechtert seit letzter Prüfung — bitte genauer prüfen</span>`;
  } else if (diff > 2) {
    boxClass = "previous-scan-better";
    diffHtml = `<span>↑ Score hat sich um ${diff} Punkte verbessert seit letzter Prüfung</span>`;
  }

  box.className = `previous-scan-box ${boxClass}`;
  box.innerHTML = `
    <div class="previous-scan-main">
      Zuletzt geprüft am <strong>${escHtml(dateLabel)}</strong> —
      Score war damals <strong>${previous.previous_score}/100</strong>
      (<strong>${escHtml(translateVerdict(previous.previous_verdict))}</strong>)
    </div>
    <div class="previous-scan-diff">${diffHtml}</div>
  `;
  box.classList.remove("hidden");
}

// ── Partial score computation (mirrors backend scoring.py) ────

function computePartialScore(results) {
  let total = 0, possible = 0;
  for (const r of results) {
    if (r.status !== "skipped" && r.status !== "na" && r.status !== "error") {
      total += r.score;
      possible += r.max_score;
    }
  }
  const normalized = possible > 0 ? Math.round((total / possible) * 100) : 0;
  const color = scoreColor(normalized, results);
  return { normalized, color };
}

function scoreColor(normalized, results) {
  // Check for critical flags in completed results
  const safebrowsingFailed = results.some(r => r.name === "safebrowsing" && r.status === "failed");
  const vtCritical = results.some(r => r.name === "virustotal" && (r.details?.malicious ?? 0) > 5);
  const newDomain = results.some(r => r.name === "whois" && r.details?.age_days > 0 && r.details?.age_days < 30);
  const llmFraud = results.some(r => {
    if (r.name !== "llm_content" || r.details?.confidence !== "high") return false;
    const answers = r.details?.answers;
    if (!Array.isArray(answers)) return false;
    const hasEvidence = (q) => answers.some(
      a => a?.question === q && a?.answer === "yes" && a?.evidence_valid === true
    );
    return hasEvidence(3) || hasEvidence(12) || (hasEvidence(1) && hasEvidence(7));
  });
  if (safebrowsingFailed || vtCritical || newDomain || llmFraud) return "red";
  if (normalized >= 75) return "green";
  if (normalized >= 50) return "yellow";
  if (normalized >= 25) return "orange";
  return "red";
}

function scoreToVerdict(normalized, color) {
  if (color === "red" && normalized < 25) return "Likely Fraudulent";
  if (color === "red") return "High Risk";
  if (normalized >= 75) return "Likely Legitimate";
  if (normalized >= 50) return "Use Caution";
  if (normalized >= 25) return "High Risk";
  return "Likely Fraudulent";
}

// ── Number animation ─────────────────────────────────────────

function animateNumber(el, target) {
  const start = parseInt(el.textContent, 10) || 0;
  if (start === target) return;
  const duration = 400;
  const startTime = performance.now();
  function step(now) {
    const t = Math.min((now - startTime) / duration, 1);
    const eased = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t; // ease-in-out
    el.textContent = Math.round(start + (target - start) * eased);
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ── Check cards ──────────────────────────────────────────────

function ensureCheckCard(name) {
  insertCheckCardInOrder(name);
}

function insertCheckCardInOrder(name) {
  let card = document.getElementById(`card-${name}`);
  if (card) return card;

  const grid = document.getElementById("checksGrid");
  const orderIdx = CHECK_ORDER.indexOf(name);
  const tier = getCheckTier(name);
  card = document.createElement("div");
  card.className = `check-card loading tier-${tier}`;
  card.id = `card-${name}`;

  let insertBefore = null;
  if (orderIdx >= 0) {
    for (let i = orderIdx + 1; i < CHECK_ORDER.length; i++) {
      const sibling = document.getElementById(`card-${CHECK_ORDER[i]}`);
      if (sibling) {
        insertBefore = sibling;
        break;
      }
    }
  }

  if (insertBefore) grid.insertBefore(card, insertBefore);
  else grid.appendChild(card);
  return card;
}

function ensureLiveResultsSection() {
  document.getElementById("liveResultsHeader")?.classList.remove("hidden");
  const hint = document.getElementById("simpleModeHint");
  if (hint && !isExpertMode()) hint.classList.remove("hidden");
  document.body.classList.add("scan-has-results");
}

function revealCompletedCheck(result) {
  ensureLiveResultsSection();
  insertCheckCardInOrder(result.name);
  updateCheckCard(result, { animate: true, scrollIntoView: true });
}

function createPlaceholderCard(name) {
  insertCheckCardInOrder(name);
  const card = document.getElementById(`card-${name}`);
  if (!card) return;
  const meta = CHECK_META[name] || { display: name, icon: "⏳" };
  const tier = getCheckTier(name);
  card.className = `check-card loading tier-${tier}`;
  card.innerHTML = `
    ${buildTierEyebrow(tier)}
    <div class="check-card-header">
      <span class="check-icon">${meta.icon}</span>
      <span class="check-name">${meta.display}</span>
      <span class="check-score-badge badge-gray">–</span>
    </div>
    <div class="check-summary" style="color:var(--c-muted)">Checking…</div>
  `;
}

function updateCheckCardRetrying(name, attempt, maxAttempts) {
  const card = insertCheckCardInOrder(name);
  if (!card) return;
  const meta = CHECK_META[name] || { display: name, icon: "🔄" };
  const tier = getCheckTier(name);

  card.className = `check-card loading tier-${tier}`;
  card.innerHTML = `
    ${buildTierEyebrow(tier)}
    <div class="check-card-header">
      <span class="check-icon">🔄</span>
      <span class="check-name">${meta.display}</span>
      <span class="check-score-badge badge-gray">↺ ${attempt + 1}/${maxAttempts}</span>
    </div>
    <div class="check-summary" style="color:var(--c-orange)">Retrying… (attempt ${attempt + 1} of ${maxAttempts})</div>
  `;
}

function updateCheckCard(result, options = {}) {
  const card = insertCheckCardInOrder(result.name);
  if (!card) return;

  const meta  = CHECK_META[result.name] || { display: result.display_name, icon: "✔" };
  const tier = getCheckTier(result.name, result);
  const color = cardColor(result.status);
  const icon  = statusIcon(result.status);

  card.className = `check-card ${result.status} tier-${tier}`;

  const badgeText = displayBadgeText(result);

  const isLlm = result.name === "llm_content";
  const isReputation = REPUTATION_CHECKS.has(result.name);
  const isSocial = result.name === "social_media";
  const isContact = result.name === "contact";
  const isZefix = result.name === "zefix";
  const triggeredHtml = isLlm
    ? buildLlmTriggeredSignalsHtml(result.details)
    : isReputation
      ? buildReputationSignalsHtml(result.details)
      : isSocial
        ? buildSocialMediaSignalsHtml(result.details)
        : isContact
          ? buildContactIbanSignalsHtml(result.details)
          : isZefix
            ? buildZefixSignalsHtml(result.details)
            : "";

  let detailsHtml = "";
  if (isLlm) {
    const breakdown = isExpertMode() ? buildScoreBreakdownHtml(result) : "";
    detailsHtml = breakdown + buildLlmContentDetailsHtml(result.details, {
      showFraudScore: allChecksComplete,
      skipped: result.status === "skipped",
      showFullCatalog: isExpertMode(),
    });
  } else if (isReputation) {
    const breakdown = isExpertMode() ? buildScoreBreakdownHtml(result) : "";
    detailsHtml = breakdown + buildReputationDetailsHtml(result.details);
  } else if (isSocial) {
    const breakdown = isExpertMode() ? buildScoreBreakdownHtml(result) : "";
    detailsHtml = breakdown + buildSocialMediaDetailsHtml(result.details);
  } else if (isContact) {
    const breakdown = isExpertMode() ? buildScoreBreakdownHtml(result) : "";
    detailsHtml = breakdown + buildContactDetailsHtml(result.details);
  } else if (isZefix) {
    const breakdown = isExpertMode() ? buildScoreBreakdownHtml(result) : "";
    detailsHtml = breakdown + buildZefixDetailsHtml(result.details);
  } else if (isExpertMode()) {
    detailsHtml = buildScoreBreakdownHtml(result) + buildDetailsHtml(result);
  }

  const detailsBlock = detailsHtml ? `
      <button class="check-details-toggle" onclick="toggleDetails(this)">Details anzeigen ▾</button>
      <div class="check-details">${detailsHtml}</div>
    ` : "";

  card.innerHTML = `
    ${buildTierEyebrow(tier)}
    <div class="check-card-header">
      <span class="check-icon">${icon}</span>
      <span class="check-name">${meta.display}</span>
      <span class="check-score-badge badge-${color}">${badgeText}</span>
    </div>
    <div class="check-summary">${escHtml(simplifySummary(result.summary))}</div>
    ${triggeredHtml}
    ${detailsBlock}
  `;

  if (options.animate) {
    card.classList.remove("check-card-reveal");
    void card.offsetWidth;
    card.classList.add("check-card-reveal");
  }

  if (options.scrollIntoView) {
    requestAnimationFrame(() => {
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }
}

function simplifySummary(summary) {
  if (isExpertMode()) return summary;
  return summary
    .replace(/\[Halbe Gewichtung\]\s*/g, "")
    .replace(/\(\d+\/\d+ pts\)/g, "")
    .slice(0, 140);
}

function buildScoreBreakdownHtml(result) {
  if (!isExpertMode()) return "";
  if (["skipped", "na", "error", "loading"].includes(result.status)) return "";

  const rawBreakdown = result.details?.score_breakdown;
  const useStored = rawBreakdown?.length && !(
    result.name === "llm_content" && rawBreakdown.some(i => /fraud probability/i.test(i.label || ""))
  );
  const items = useStored ? rawBreakdown : inferScoreBreakdown(result);

  if (!items.length) return "";

  let rows = "";
  for (const item of items) {
    const earned = item.points > 0;
    rows += `
      <li class="score-breakdown-item ${earned ? "earned" : "missed"}">
        <span class="score-breakdown-label">${escHtml(item.label)}</span>
        <span class="score-breakdown-pts">+${item.points}/${item.max_points}</span>
      </li>`;
  }

  return `
    <div class="score-breakdown">
      <div class="score-breakdown-title">Score breakdown</div>
      <ul class="score-breakdown-list">${rows}</ul>
      <div class="score-breakdown-total">
        Total: <strong>+${result.score}/${result.max_score}</strong>
      </div>
    </div>`;
}

function inferScoreBreakdown(result) {
  const d = result.details || {};
  const items = [];

  switch (result.name) {
    case "whois": {
      if (d.private) {
        items.push({ label: "Registration date hidden", points: 0, max_points: 8 });
        break;
      }
      const age = d.age_days ?? 0;
      if (age >= 5 * 365) items.push({ label: "Domain age ≥ 5 years", points: 8, max_points: 8 });
      else if (age >= 2 * 365) items.push({ label: "Domain age ≥ 2 years", points: 6, max_points: 8 });
      else if (age >= 365) items.push({ label: "Domain age ≥ 1 year", points: 4, max_points: 8 });
      else if (age >= 180) items.push({ label: "Domain age ≥ 6 months", points: 2, max_points: 8 });
      else items.push({ label: "Domain age < 6 months", points: 0, max_points: 8 });
      break;
    }
    case "ssl": {
      if (!d.valid && result.score === 0) {
        items.push({ label: "Valid trusted certificate", points: 0, max_points: 4 });
      } else if (d.days_until_expiry > 0 && d.days_until_expiry <= 30) {
        items.push({ label: "Valid certificate (expiring within 30 days)", points: 2, max_points: 4 });
      } else if (result.score === 4) {
        items.push({ label: "Valid trusted certificate", points: 4, max_points: 4 });
      } else {
        items.push({ label: "SSL certificate", points: result.score, max_points: 4 });
      }
      break;
    }
    case "hosting": {
      if (result.score === 4) {
        items.push({ label: "Reputable hosting / cloud provider", points: 4, max_points: 4 });
      } else if (result.score === 2) {
        items.push({ label: "Acceptable hosting region", points: 2, max_points: 4 });
      } else if (result.score === 1) {
        items.push({ label: "High-risk / offshore hosting signal", points: 1, max_points: 4 });
      } else {
        items.push({ label: "Hosting location", points: result.score, max_points: 4 });
      }
      break;
    }
    case "wayback": {
      if (!d.archived) {
        items.push({ label: "Not archived in Wayback Machine", points: 0, max_points: 3 });
        break;
      }
      const age = d.age_days ?? 0;
      if (age >= 3 * 365) items.push({ label: "First archived ≥ 3 years ago", points: 3, max_points: 3 });
      else if (age >= 365) items.push({ label: "First archived ≥ 1 year ago", points: 2, max_points: 3 });
      else if (age >= 180) items.push({ label: "First archived ≥ 6 months ago", points: 1, max_points: 3 });
      else if (age >= 30) items.push({ label: "First archived ≥ 30 days ago", points: 1, max_points: 3 });
      else items.push({ label: "Recently archived (< 30 days)", points: 0, max_points: 3 });
      break;
    }
    case "crt": {
      items.push({ label: "Certificate history present", points: result.score, max_points: 1 });
      break;
    }
    case "hsts": {
      if (d.preloaded) {
        items.push({ label: "On HSTS preload list", points: 3, max_points: 3 });
      } else if ((d.max_age ?? 0) >= 31536000) {
        items.push({ label: "Strong HSTS (max-age ≥ 1 year)", points: result.score, max_points: 3 });
      } else if (d.has_hsts_header) {
        items.push({ label: "HSTS header present", points: result.score, max_points: 3 });
      } else {
        items.push({ label: "HSTS configured", points: 0, max_points: 3 });
      }
      break;
    }
    case "contact": {
      if (result.score >= 12) items.push({ label: "Strong contact signals", points: result.score, max_points: 14 });
      else if (result.score >= 7) items.push({ label: "Partial contact information", points: result.score, max_points: 14 });
      else items.push({ label: "Weak or missing contact info", points: result.score, max_points: 14 });
      break;
    }
    case "google_reviews": {
      if (!d.profile_found) {
        items.push({ label: "Kein Google-Profil", points: 0, max_points: 12 });
        break;
      }
      if (Array.isArray(d.score_breakdown) && d.score_breakdown.length) {
        items.push(...d.score_breakdown);
        break;
      }
      items.push({
        label: `Google ${d.rating ?? "n/a"}/5, ${d.total_reviews ?? 0} Bewertungen`,
        points: result.score,
        max_points: 12,
      });
      break;
    }
    case "social_media": {
      if (!d.links_found) {
        items.push({ label: "Keine Social-Media-Links", points: 0, max_points: 8 });
        break;
      }
      if (Array.isArray(d.score_breakdown) && d.score_breakdown.length) {
        items.push(...d.score_breakdown);
      }
      items.push({
        label: `${Object.keys(d.links || {}).length} Kanäle verlinkt`,
        points: result.score,
        max_points: 8,
      });
      break;
    }
    case "trustpilot": {
      if (!d.profile_found) {
        items.push({ label: "Kein Trustpilot-Profil", points: 0, max_points: 12 });
        break;
      }
      if (Array.isArray(d.score_breakdown) && d.score_breakdown.length) {
        items.push(...d.score_breakdown);
        break;
      }
      items.push({
        label: `TrustScore ${d.trust_score ?? "n/a"}, ${d.total_reviews ?? 0} Bewertungen`,
        points: result.score,
        max_points: 12,
      });
      break;
    }
    case "virustotal": {
      const mal = d.malicious ?? 0;
      const susp = d.suspicious ?? 0;
      const total = d.total_engines ?? (mal + susp + (d.harmless ?? 0) + (d.undetected ?? 0));
      const vtMax = result.max_score || 10;
      if (d.not_found) {
        items.push({ label: "Domain not in VirusTotal database", points: 5, max_points: vtMax });
      } else if (mal === 0 && susp === 0) {
        items.push({ label: `Clean — 0/${total} engines flagged`, points: vtMax, max_points: vtMax });
      } else {
        const pct = d.clean_ratio_percent ?? (total ? Math.round((total - mal - susp) / total * 100) : 0);
        items.push({
          label: `${mal}/${total} malicious, ${susp} suspicious (${pct}% clean)`,
          points: result.score,
          max_points: vtMax,
        });
      }
      break;
    }
    case "safebrowsing": {
      if (d.flagged) items.push({ label: "Threat detected by Safe Browsing", points: 0, max_points: 15 });
      else items.push({ label: "No Safe Browsing threats", points: result.score, max_points: 15 });
      break;
    }
    case "urlscan": {
      if (result.score === 1) items.push({ label: "Clean URLScan report", points: 1, max_points: 1 });
      else if (result.score > 0) items.push({ label: "URLScan scan completed", points: result.score, max_points: 1 });
      else items.push({ label: "URLScan scan", points: 0, max_points: 1 });
      break;
    }
    case "zefix": {
      if (Array.isArray(d.score_breakdown) && d.score_breakdown.length) {
        items.push(...d.score_breakdown);
        break;
      }
      if (result.score === 10) items.push({ label: "Active Swiss company found", points: 10, max_points: 10 });
      else if (result.score === 2) items.push({ label: "Company found (inactive status)", points: 2, max_points: 10 });
      else items.push({ label: "Swiss company registry match", points: 0, max_points: 10 });
      break;
    }
    case "llm_content": {
      const weight = d.score_weight === 0.5 ? " (halbe Gewichtung)" : "";
      if (result.status === "skipped") {
        items.push({ label: `Skipped — low AI confidence${weight}`, points: 0, max_points: 16 });
        break;
      }
      if (isLegacyLlmDetails(d)) {
        items.push({ label: "Altes Analyseformat", points: result.score, max_points: 16 });
        break;
      }
      const answers = d.answers;
      if (Array.isArray(answers) && answers.length) {
        const hits = answers.filter(a => a.answer === "yes");
        if (!hits.length) {
          items.push({ label: "Keine Risikosignale erkannt", points: result.score, max_points: 16 });
          break;
        }
        for (const entry of hits) {
          const q = entry.question;
          const pen = LLM_QUESTION_PENALTIES[q] || 0;
          items.push({
            label: `✗ ${getLlmQuestionLabel(q, d.question_texts)} (−${pen} Pkt.)`,
            points: 0,
            max_points: pen,
          });
        }
        break;
      }
      items.push({ label: "KI-Fragenkatalog", points: result.score, max_points: 16 });
      break;
    }
    default:
      if (result.max_score > 0) {
        items.push({ label: result.display_name || result.name, points: result.score, max_points: result.max_score });
      }
  }

  return items;
}

function buildDetailsHtml(result) {
  if (!isExpertMode()) return "";
  const d = result.details;
  if (!d || Object.keys(d).length === 0) return "";

  if (result.name === "llm_content") {
    return buildLlmContentDetailsHtml(d, {
      showFraudScore: allChecksComplete,
      skipped: result.status === "skipped",
    });
  }

  if (result.name === "trustpilot" || result.name === "google_reviews") {
    return buildReputationDetailsHtml(d);
  }

  if (result.name === "social_media") {
    return buildSocialMediaDetailsHtml(d);
  }

  if (result.name === "zefix") {
    return buildZefixDetailsHtml(d);
  }

  const skipKeys = new Set(["skipped", "not_found", "archived", "flagged", "private", "searched", "score_breakdown", "analysis", "review_sample", "warning_flags", "links", "profiles", "place_types", "reviews_fetched", "recent_publications", "ehraid", "is_young_company", "is_new_registration_only", "mutation_analysis", "publication_count", "latest_mutation_date", "days_since_last_mutation"]);
  const entries = Object.entries(d).filter(([k, v]) => {
    if (skipKeys.has(k)) return false;
    if (v === null || v === undefined || v === "" || v === false) return false;
    return true;
  });

  if (entries.length === 0) return "";

  let html = "<dl>";
  for (const [key, value] of entries) {
    if (key === "screenshot_url" && value) {
      html += `<dt colspan="2"></dt><dd colspan="2"><img src="${escHtml(value)}" class="screenshot-thumb" alt="Screenshot" loading="lazy"></dd>`;
      continue;
    }
    if (key === "zefix_url" && value) {
      html += `<dt>Zefix</dt><dd><a href="${escHtml(value)}" target="_blank" rel="noopener">Open in Zefix ↗</a></dd>`;
      continue;
    }
    if (key === "wayback_url" && value) {
      html += `<dt>Archive</dt><dd><a href="${escHtml(value)}" target="_blank" rel="noopener">Browse archive ↗</a></dd>`;
      continue;
    }
    if (key === "result_url" && value) {
      html += `<dt>Report</dt><dd><a href="${escHtml(value)}" target="_blank" rel="noopener">URLScan report ↗</a></dd>`;
      continue;
    }
    if (key === "setup_url" || key === "note") continue;

    const label = key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    let display = Array.isArray(value) ? value.join(", ") : String(value);
    if (/_date$|_at$|^date$/i.test(key) || /^\d{4}-\d{2}-\d{2}/.test(display)) {
      display = key.endsWith("_at") || /T\d{2}:/.test(display)
        ? formatDateTimeDisplay(display)
        : formatDateDisplay(display);
    } else if (typeof value === "string") {
      display = formatDatesInText(display);
    }
    if (display.length > 160) display = display.slice(0, 157) + "…";
    html += `<dt>${escHtml(label)}</dt><dd>${escHtml(display)}</dd>`;
  }
  html += "</dl>";
  return html;
}

function buildZefixSignalsHtml(d) {
  if (!d || d.searched === false) return "";

  const warnings = Array.isArray(d.warning_flags) ? d.warning_flags : [];
  const pubs = Array.isArray(d.recent_publications) ? d.recent_publications : [];
  const deductWarnings = warnings.filter((w) => !w.startsWith("Neueintragung vom"));

  let html = "";
  if (deductWarnings.length) {
    html += `<div class="tp-hit-signals"><div class="tp-hit-signals-title">Register-Warnsignale (${deductWarnings.length})</div><ul class="tp-hit-signals-list">`;
    for (const w of deductWarnings) {
      html += `<li class="tp-hit-signal-item"><span class="tp-hit-icon">⚠</span><span>${escHtml(formatDatesInText(w))}</span></li>`;
    }
    html += `</ul></div>`;
  }

  if (pubs.length) {
    html += `<div class="zefix-mutations"><div class="zefix-mutations-title">SHAB / Handelsregister (${d.publication_count ?? pubs.length})</div><ul class="zefix-mutations-list">`;
    for (const pub of pubs.slice(0, 3)) {
      const types = Array.isArray(pub.types_de) && pub.types_de.length
        ? pub.types_de.join(", ")
        : (Array.isArray(pub.types) ? pub.types.join(", ") : "Meldung");
      html += `<li class="zefix-mutation-item"><span class="zefix-mutation-date">${escHtml(formatDateDisplay(pub.date) || "n/a")}</span> — <span>${escHtml(types)}</span></li>`;
    }
    if ((d.publication_count ?? pubs.length) > 3) {
      html += `<li class="zefix-mutation-more">… und ${(d.publication_count ?? pubs.length) - 3} weitere Meldungen</li>`;
    }
    html += `</ul></div>`;
  } else if (d.mutation_analysis) {
    html += `<div class="zefix-mutations-clear">${escHtml(formatDatesInText(d.mutation_analysis))}</div>`;
  }

  return html;
}

function buildZefixDetailsHtml(d) {
  if (!d) return "";

  let html = "<dl>";
  const fields = [
    ["name", "Firma"],
    ["uid", "UID"],
    ["status", "Status"],
    ["canton", "Kanton"],
    ["publication_count", "SHAB-Meldungen"],
    ["latest_mutation_date", "Letzte Meldung"],
    ["days_since_last_mutation", "Tage seit letzter Meldung"],
    ["mutation_analysis", "Mutationsanalyse"],
  ];
  for (const [key, label] of fields) {
    const value = d[key];
    if (value === null || value === undefined || value === "") continue;
    const shown = key.endsWith("_date") || key === "latest_mutation_date"
      ? formatDateDisplay(value)
      : (typeof value === "string" ? formatDatesInText(value) : String(value));
    html += `<dt>${escHtml(label)}</dt><dd>${escHtml(shown)}</dd>`;
  }
  if (d.zefix_url) {
    html += `<dt>Zefix</dt><dd><a href="${escHtml(d.zefix_url)}" target="_blank" rel="noopener">Im Handelsregister öffnen ↗</a>`;
    if (d.name) {
      html += ` · <a href="/hr-network?company=${encodeURIComponent(d.name)}">HR-Netzwerk ↗</a>`;
    }
    html += `</dd>`;
  } else if (d.name) {
    html += `<dt>HR-Netz</dt><dd><a href="/hr-network?company=${encodeURIComponent(d.name)}">Firmennetzwerk anzeigen ↗</a></dd>`;
  }
  html += "</dl>";

  const pubs = Array.isArray(d.recent_publications) ? d.recent_publications : [];
  if (pubs.length) {
    html += `<div class="zefix-mutations-detail"><div class="zefix-mutations-title">Letzte SHAB-Publikationen</div><ul class="zefix-mutations-list">`;
    for (const pub of pubs) {
      const types = Array.isArray(pub.types_de) && pub.types_de.length
        ? pub.types_de.join(", ")
        : (Array.isArray(pub.types) ? pub.types.join(", ") : "Meldung");
      const msg = pub.message_short ? `<div class="zefix-mutation-msg">${escHtml(pub.message_short)}</div>` : "";
      html += `<li class="zefix-mutation-item"><strong>${escHtml(formatDateDisplay(pub.date) || "n/a")}</strong> — ${escHtml(types)}${msg}</li>`;
    }
    html += `</ul></div>`;
  }

  return html;
}

function buildContactIbanSignalsHtml(d) {
  if (!d) return "";
  const ibans = Array.isArray(d.ibans) ? d.ibans : [];
  const warnings = Array.isArray(d.iban_warning_flags) ? d.iban_warning_flags : [];

  if (!ibans.length && !warnings.length) return "";

  let html = "";
  if (ibans.length) {
    const locale = d.site_payment_locale || "?";
    const localeHint = d.swiss_site_context
      ? "Schweizer Website-Kontext"
      : locale !== "UNKNOWN"
        ? `Website-Kontext: ${locale}`
        : "Neutraler Website-Kontext";
    html += `<div class="contact-iban-section"><div class="contact-iban-title">Zahlungsverbindungen (${localeHint})</div><ul class="contact-iban-list">`;
    for (const item of ibans) {
      const cls = warnings.length ? "contact-iban-item contact-iban-warn" : "contact-iban-item contact-iban-ok";
      html += `<li class="${cls}">
        <span class="contact-iban-value">${escHtml(item.masked || item.formatted || "")}</span>
        <span class="contact-iban-meta">${escHtml(item.country_label || item.country_code || "")} · ${escHtml(item.source || "")}</span>
      </li>`;
    }
    html += `</ul></div>`;
  }

  if (warnings.length) {
    html += `<div class="tp-hit-signals"><div class="tp-hit-signals-title">IBAN-Hinweise</div><ul class="tp-hit-signals-list">`;
    for (const w of warnings) {
      html += `<li class="tp-hit-signal-item"><span class="tp-hit-icon">⚠</span><span>${escHtml(w)}</span></li>`;
    }
    html += `</ul></div>`;
  }
  return html;
}

function buildContactDetailsHtml(d) {
  if (!d) return "";
  const skipKeys = new Set([
    "skipped", "score_breakdown", "ibans", "iban_warning_flags",
    "iban_count", "rendered_with",
  ]);
  const entries = Object.entries(d).filter(([k, v]) => {
    if (skipKeys.has(k)) return false;
    if (v === null || v === undefined || v === "" || v === false) return false;
    return true;
  });
  if (!entries.length) return "";
  let html = "<dl>";
  for (const [key, value] of entries) {
    const label = key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    let display = Array.isArray(value) ? value.join(", ") : String(value);
    if (/_date$|_at$|^date$/i.test(key) || /^\d{4}-\d{2}-\d{2}/.test(display)) {
      display = key.endsWith("_at") || /T\d{2}:/.test(display)
        ? formatDateTimeDisplay(display)
        : formatDateDisplay(display);
    } else if (typeof value === "string") {
      display = formatDatesInText(display);
    }
    if (display.length > 160) display = display.slice(0, 157) + "…";
    html += `<dt>${escHtml(label)}</dt><dd>${escHtml(display)}</dd>`;
  }
  html += "</dl>";
  return html;
}

function buildNegativeReviewSignalsHtml(d) {
  const neg = d?.negative_review_analysis;
  const negReviews = d?.negative_reviews || [];

  if (!neg || neg.skipped) {
    if (negReviews.length === 0 && d?.profile_found) {
      return `<div class="tp-signals-clear">Keine 1–2-Sterne-Reviews in der Stichprobe</div>`;
    }
    return "";
  }

  const severity = neg.overall_severity || "none";
  if (severity === "none") {
    return `<div class="tp-signals-clear">Negative Reviews (KI): kein Betrugsrisiko erkannt</div>`;
  }

  const isHigh = severity === "high";
  const title = isHigh
    ? `KI: Betrugsverdacht in negativen Reviews (${neg.fraud_signal_count || 0} Signal(e))`
    : "KI: Negative Reviews mit berechtigter Kritik";
  const boxClass = isHigh ? "tp-hit-signals" : "tp-warn-signals";

  let html = `<div class="${boxClass}"><div class="tp-hit-signals-title">${escHtml(title)}</div>`;
  if (neg.summary) {
    html += `<p class="tp-neg-summary">${escHtml(neg.summary)}</p>`;
  }
  html += `<ul class="tp-hit-signals-list">`;
  for (const entry of (neg.reviews || []).filter((r) => r.severity && r.severity !== "none")) {
    const icon = entry.severity === "high" ? "✗" : "⚠";
    const quote = entry.evidence_quote
      ? ` — «${escHtml(entry.evidence_quote.slice(0, 100))}»`
      : "";
    html += `<li class="tp-hit-signal-item"><span class="tp-hit-icon">${icon}</span><span>${escHtml(entry.summary || "")}${quote}</span></li>`;
  }
  html += `</ul></div>`;
  return html;
}

function buildReputationSignalsHtml(d) {
  if (!d?.profile_found) {
    return `<div class="tp-no-profile">Kein Bewertungsprofil vorhanden</div>`;
  }

  let html = "";
  const negHtml = buildNegativeReviewSignalsHtml(d);
  if (negHtml) html += negHtml;

  const warnings = d.warning_flags || [];
  const metaWarnings = warnings.filter((w) => {
    const negSum = d.negative_review_analysis?.summary;
    return !negSum || w !== negSum;
  });
  const rating = d.trust_score ?? d.rating ?? "n/a";

  if (!metaWarnings.length && !negHtml.includes("tp-hit-signals") && !negHtml.includes("tp-warn-signals")) {
    if (!negHtml) {
      return `<div class="tp-signals-clear">Rating ${escHtml(rating)}/5 — ${escHtml(d.total_reviews ?? 0)} Bewertungen, keine Fassaden-Warnsignale</div>`;
    }
    return html;
  }

  if (metaWarnings.length) {
    html += `<div class="tp-hit-signals"><div class="tp-hit-signals-title">Review-Warnsignale (${metaWarnings.length})</div><ul class="tp-hit-signals-list">`;
    for (const w of metaWarnings) {
      html += `<li class="tp-hit-signal-item"><span class="tp-hit-icon">⚠</span><span>${escHtml(w)}</span></li>`;
    }
    html += `</ul></div>`;
  }
  return html || `<div class="tp-signals-clear">Rating ${escHtml(rating)}/5 — ${escHtml(d.total_reviews ?? 0)} Bewertungen</div>`;
}

function buildReputationDetailsHtml(d) {
  if (!d || !d.profile_found) {
    return `<div class="tp-no-profile">Kein Bewertungsprofil für diese Domain.</div>`;
  }

  const a = d.analysis || {};
  const rating = d.trust_score ?? d.rating ?? "n/a";
  let html = `<div class="tp-details">`;
  if (d.profile_url) {
    html += `<div class="tp-profile-link"><a href="${escHtml(d.profile_url)}" target="_blank" rel="noopener">Profil öffnen ↗</a></div>`;
  }
  html += `<dl class="tp-metrics">`;
  html += `<dt>Rating</dt><dd>${escHtml(rating)}/5 (${escHtml(d.total_reviews ?? 0)} gesamt)</dd>`;
  if (d.display_name) {
    html += `<dt>Name</dt><dd>${escHtml(d.display_name)}</dd>`;
  }
  if (d.is_collecting_reviews != null) {
    html += `<dt>Bewertungseinladungen</dt><dd>${d.is_collecting_reviews ? "Aktiv" : "Keine Aufzeichnungen"}</dd>`;
  }
  html += `<dt>Stichprobe analysiert</dt><dd>${escHtml(a.sample_size ?? 0)} Reviews</dd>`;
  if (a.single_review_account_known >= 3) {
    html += `<dt>Einmal-Reviewer</dt><dd>${escHtml(a.single_review_account_pct ?? 0)}%</dd>`;
  }
  if (a.verified_known >= 3) {
    html += `<dt>Verifiziert</dt><dd>${escHtml(a.verified_pct ?? 0)}%</dd>`;
  }
  if (a.avg_days_between_reviews != null) {
    html += `<dt>Ø Abstand</dt><dd>${escHtml(a.avg_days_between_reviews)} Tage</dd>`;
  }
  if (d.is_bad_category) {
    html += `<dt>Risikobranche</dt><dd>Ja (z.B. Finanz/Krypto)</dd>`;
  }
  html += `</dl>`;

  const sample = d.review_sample;
  if (Array.isArray(sample) && sample.length) {
    html += `<div class="tp-review-sample"><strong>Review-Stichprobe</strong><ul>`;
    for (const rev of sample) {
      const verified = rev.verified ? "✓ verifiziert" : "○ nicht verifiziert";
      const authorReviews = rev.author_total_reviews === 1 ? "1 Review gesamt"
        : rev.author_total_reviews != null ? `${rev.author_total_reviews} Reviews gesamt` : "";
      const date = rev.published ? formatDateDisplay(rev.published) : "";
      html += `<li class="tp-review-row">
        <span class="tp-review-stars">${"★".repeat(rev.rating || 0)}</span>
        <span class="tp-review-meta">${escHtml(rev.author || "?")}${authorReviews ? ` — ${escHtml(authorReviews)}` : ""} — ${escHtml(verified)}</span>
        ${date ? `<span class="tp-review-date">${escHtml(date)}</span>` : ""}
        ${rev.title ? `<span class="tp-review-title">${escHtml(rev.title)}</span>` : ""}
        ${rev.text ? `<span class="tp-review-text">${escHtml(rev.text)}</span>` : ""}
      </li>`;
    }
    html += `</ul></div>`;
  }

  const negative = d.negative_reviews;
  if (Array.isArray(negative) && negative.length) {
    html += `<div class="tp-review-sample tp-negative-reviews"><strong>Negative Reviews (1–2★)</strong><ul>`;
    for (const rev of negative) {
      const date = rev.published ? formatDateDisplay(rev.published) : "";
      html += `<li class="tp-review-row tp-review-negative">
        <span class="tp-review-stars tp-stars-bad">${"★".repeat(rev.rating || 0)}</span>
        <span class="tp-review-meta">${escHtml(rev.author || "?")}${date ? ` — ${escHtml(date)}` : ""}</span>
        ${rev.title ? `<span class="tp-review-title">${escHtml(rev.title)}</span>` : ""}
        ${rev.text ? `<span class="tp-review-text">${escHtml(rev.text)}</span>` : ""}
        ${rev.company_reply ? `<span class="tp-review-reply">Antwort: ${escHtml(rev.company_reply)}</span>` : ""}
      </li>`;
    }
    html += `</ul></div>`;
  }

  const negAi = d.negative_review_analysis;
  if (negAi && !negAi.skipped && negAi.reviews?.length) {
    html += `<div class="tp-neg-ai"><strong>KI-Bewertung negativer Reviews</strong><p>${escHtml(negAi.summary || "")}</p><ul>`;
    for (const entry of negAi.reviews) {
      if (!entry.summary && entry.severity === "none") continue;
      html += `<li><span class="tp-neg-sev-${escHtml(entry.severity || "none")}">${escHtml((entry.severity || "none").toUpperCase())}</span> ${escHtml(entry.summary || "")}`;
      if (entry.evidence_quote) {
        html += ` <em>«${escHtml(entry.evidence_quote.slice(0, 120))}»</em>`;
      }
      html += `</li>`;
    }
    html += `</ul></div>`;
  }
  html += `</div>`;
  return html;
}

function buildSocialMediaSignalsHtml(d) {
  if (!d?.links_found) {
    return `<div class="tp-no-profile">Keine Social-Media-Links auf der Startseite</div>`;
  }
  const warnings = d.warning_flags || [];
  if (!warnings.length) {
    const n = Object.keys(d.links || {}).length;
    return `<div class="tp-signals-clear">${n} Social-Media-Kanal/Kanäle verlinkt — keine Warnsignale</div>`;
  }
  let html = `<div class="tp-hit-signals"><div class="tp-hit-signals-title">Social-Media-Warnsignale (${warnings.length})</div><ul class="tp-hit-signals-list">`;
  for (const w of warnings) {
    html += `<li class="tp-hit-signal-item"><span class="tp-hit-icon">⚠</span><span>${escHtml(w)}</span></li>`;
  }
  html += `</ul></div>`;
  return html;
}

function buildSocialMediaDetailsHtml(d) {
  if (!d?.links_found) {
    return `<div class="tp-no-profile">Keine Social-Media-Links gefunden.</div>`;
  }
  let html = `<div class="tp-details"><dl class="tp-metrics">`;
  for (const [platform, url] of Object.entries(d.links || {})) {
    html += `<dt>${escHtml(platform)}</dt><dd><a href="${escHtml(url)}" target="_blank" rel="noopener">${escHtml(url)}</a></dd>`;
  }
  html += `</dl>`;

  const profiles = d.profiles || [];
  if (profiles.length) {
    html += `<div class="tp-review-sample"><strong>Profil-Analyse</strong><ul>`;
    for (const p of profiles) {
      html += `<li class="tp-review-row">
        <span class="tp-review-meta"><strong>${escHtml(p.label || p.platform)}</strong> — ${escHtml(p.status || "?")}</span>
        <span class="tp-review-title">${escHtml(p.note || "")}</span>
        ${p.followers != null ? `<span class="tp-review-date">${escHtml(p.followers.toLocaleString())} Follower</span>` : ""}
      </li>`;
    }
    html += `</ul></div>`;
  }
  html += `</div>`;
  return html;
}

function buildTrustpilotSignalsHtml(d) {
  return buildReputationSignalsHtml(d);
}

function buildTrustpilotDetailsHtml(d) {
  return buildReputationDetailsHtml(d);
}

function buildLlmTriggeredSignalsHtml(d) {
  if (!d) return "";
  if (isLegacyLlmDetails(d)) {
    return `<div class="llm-legacy-notice">Altes Format — keine Detailauswertung verfügbar</div>`;
  }
  if (!Array.isArray(d.answers) || !d.answers.length) return "";

  const hits = d.answers.filter(a => a.answer === "yes");
  if (!hits.length) {
    return `<div class="llm-hit-signals llm-hit-signals-clear">Keine Risikosignale erkannt</div>`;
  }

  const questionTexts = d.question_texts || {};
  let html = `<div class="llm-hit-signals"><div class="llm-hit-signals-title">Ausgelöste Signale (${hits.length})</div><ul class="llm-hit-signals-list">`;
  for (const entry of hits) {
    const label = getLlmQuestionLabel(entry.question, questionTexts);
    const pen = LLM_QUESTION_PENALTIES[entry.question] || 0;
    const reasoning = entry.reasoning
      ? `<span class="llm-hit-reasoning">${escHtml(entry.reasoning)}</span>`
      : "";
    const evidence = entry.evidence_quote && entry.evidence_valid
      ? `<span class="llm-hit-evidence">«${escHtml(entry.evidence_quote)}»</span>`
      : "";
    html += `<li class="llm-hit-signal-item">
      <span class="llm-hit-icon">✗</span>
      <span class="llm-hit-body">
        <span class="llm-hit-label">${escHtml(label)}</span>
        ${reasoning}
        ${evidence}
      </span>
      <span class="llm-hit-penalty">−${pen}</span>
    </li>`;
  }
  html += `</ul></div>`;
  return html;
}

function buildLlmContentDetailsHtml(d, options = {}) {
  const showFraudScore = options.showFraudScore !== false;

  const FRAUD_CATEGORY_LABELS = {
    investment_fraud: "Anlagebetrug",
    precious_metals_fraud: "Edelmetall-Betrug",
    loan_fraud: "Kreditbetrug",
    phishing_impersonation: "Phishing/Identitätsmissbrauch",
    support_scam: "Support-/Tech-Betrug (gefälschte Warnung)",
    booking_scam: "Vorschussbetrug (Ferienwohnung/Buchung)",
    marketplace_scam: "Online-Marktplatz-Betrug",
    fake_shop: "Fake-Shop",
    romance_scam_support: "Romance-Scam-Unterstützung",
    pyramid_mlm: "Pyramidensystem/MLM",
    transaction_mismatch: "Betrag/Zweck passt nicht zum Webseitenangebot",
    general_suspicious: "Mehrere Warnsignale, unklares Muster",
    none_detected: "Keine Kategorie erkannt",
    unclear: "Keine klare Einschätzung möglich",
  };

  const DISMISSIBLE_CATEGORIES = new Set([
    "investment_fraud", "precious_metals_fraud", "loan_fraud",
    "phishing_impersonation", "support_scam", "booking_scam", "marketplace_scam",
    "fake_shop", "romance_scam_support",
    "pyramid_mlm", "general_suspicious",
  ]);

  const CONFIDENCE_LABELS = { high: "hoch", medium: "mittel", low: "niedrig" };

  const showFullCatalog = options.showFullCatalog !== false;

  if (isLegacyLlmDetails(d)) {
    let legacyHtml = `<div class="llm-legacy-notice">Altes Format — keine Detailauswertung verfügbar</div>`;
    if (d.content_type) {
      legacyHtml += `<div class="llm-content-type">Erkannter Seitentyp: ${escHtml(d.content_type)}</div>`;
    }
    return legacyHtml;
  }

  let html = "";

  if (options.skipped || d.skipped_reason) {
    html += `<div class="llm-skipped-notice">AI-Check übersprungen — niedrige Konfidenz / unzureichender Inhalt (Werte zur Diagnose)</div>`;
  }

  if (d.user_dismissed) {
    html += `<div class="llm-dismissed-notice">✓ Vom Benutzer als Fehlalarm markiert</div>`;
    if (d.user_feedback) {
      html += `<div class="llm-user-feedback-text">«${escHtml(d.user_feedback)}»</div>`;
    }
    if (d.original_fraud_category && FRAUD_CATEGORY_LABELS[d.original_fraud_category]) {
      html += `<div class="llm-original-category">Ursprüngliche KI-Kategorie: ${escHtml(FRAUD_CATEGORY_LABELS[d.original_fraud_category])}</div>`;
    }
  }

  if (Array.isArray(d.cross_check_overrides) && d.cross_check_overrides.length) {
    html += `<div class="llm-cross-check-notes"><strong>Cross-Check-Anpassungen</strong><ul>`;
    for (const note of d.cross_check_overrides) {
      html += `<li>${escHtml(note)}</li>`;
    }
    html += `</ul></div>`;
  }

  if (d.pages_analyzed) {
    html += `<div class="llm-pages-meta">${d.pages_analyzed} Seite(n) analysiert (Startseite + Rechts-/Kontaktseiten)</div>`;
  }

  if (Array.isArray(d.answers) && d.answers.length && showFullCatalog) {
    const questionTexts = d.question_texts || {};
    const asked = Array.isArray(d.questions_asked) && d.questions_asked.length
      ? d.questions_asked
      : d.answers.map((a) => a.question);
    const visibleAnswers = d.answers.filter((entry) => asked.includes(entry.question));
    html += `<div class="llm-indicators-section"><strong>Alle Fragen (${asked.length})</strong><ul class="llm-question-list">`;
    for (const entry of visibleAnswers) {
      const q = entry.question;
      const answer = entry.answer || "unclear";
      const label = getLlmQuestionLabel(q, questionTexts);
      let icon, rowClass;
      if (answer === "yes") {
        icon = "✗";
        rowClass = "llm-question-yes";
      } else if (answer === "no") {
        icon = "✓";
        rowClass = "llm-question-no";
      } else {
        icon = "°";
        rowClass = "llm-question-unclear";
      }
      const reasoning = entry.reasoning ? escHtml(entry.reasoning) : "";
      const evidence = entry.evidence_quote
        ? `<span class="llm-question-evidence">«${escHtml(entry.evidence_quote)}»</span>`
        : "";
      const overrideTag = entry.overridden
        ? `<span class="llm-question-override">Cross-Check</span>`
        : "";
      html += `<li class="llm-question-row ${rowClass}">
        <span class="llm-question-icon">${icon}</span>
        <div class="llm-question-body">
          <span class="llm-question-label">${escHtml(label)} ${overrideTag}</span>
          ${reasoning ? `<span class="llm-question-reasoning">${reasoning}</span>` : ""}
          ${evidence}
        </div>
      </li>`;
    }
    html += `</ul></div>`;
  }

  const displayCategory = d.user_dismissed
    ? (d.original_fraud_category || d.fraud_category)
    : d.fraud_category;

  if (displayCategory && FRAUD_CATEGORY_LABELS[displayCategory] && !d.user_dismissed) {
    const catClass = displayCategory === "none_detected" ? "llm-category-none"
      : displayCategory === "unclear" ? "llm-category-unclear"
      : "llm-category-fraud";
    html += `<div class="llm-category-badge ${catClass}">${escHtml(FRAUD_CATEGORY_LABELS[displayCategory])}</div>`;
  }

  if (d.confidence && CONFIDENCE_LABELS[d.confidence] && showFraudScore) {
    html += `<div class="llm-confidence-row"><span class="llm-confidence-badge llm-confidence-${escHtml(d.confidence)}">Konfidenz: ${escHtml(CONFIDENCE_LABELS[d.confidence])}</span></div>`;
  }

  if (d.backend) {
    html += `<div class="llm-backend-row">Backend: ${escHtml(d.backend)}</div>`;
  }

  if (showFraudScore && !d.user_dismissed && canShowLlmDismissForm(d, DISMISSIBLE_CATEGORIES)) {
    html += `
      <div class="llm-feedback-section">
        <button type="button" class="llm-dismiss-btn" onclick="toggleLlmFeedbackForm(this)">
          Fehlalarm melden
        </button>
        <div class="llm-feedback-form hidden">
          <label class="llm-feedback-label">Warum passt diese Einschätzung nicht?</label>
          <textarea class="llm-feedback-input" rows="3" placeholder="z.B. Die Seite hat mit Edelmetallhandel nichts zu tun — es ist ein Nachrichtenportal."></textarea>
          <button type="button" class="llm-feedback-submit" onclick="submitLlmFeedback(this)">
            Melden &amp; Flag entfernen
          </button>
        </div>
      </div>`;
  }

  if (d.content_type) {
    html += `<div class="llm-content-type">Erkannter Seitentyp: ${escHtml(d.content_type)}</div>`;
  }

  if (d.content_diagnostics) {
    const cd = d.content_diagnostics;
    html += `<div class="content-diagnostics-box">`;
    if (cd.likely_blocked) {
      html += `<div class="content-diagnostics-warning">⚠ Mögliche Scraping-Einschränkung erkannt</div>`;
      if (Array.isArray(cd.reasons) && cd.reasons.length) {
        html += `<ul class="content-diagnostics-reasons">`;
        for (const reason of cd.reasons) {
          html += `<li>${escHtml(reason)}</li>`;
        }
        html += `</ul>`;
      }
    }
    html += `<div class="content-diagnostics-stats">Analysierter Text: ${escHtml(cd.extracted_text_length)} von ${escHtml(cd.raw_html_length)} Zeichen (${escHtml(cd.text_ratio_percent)}%)</div>`;
    html += `</div>`;
  }

  return html;
}

function canShowLlmDismissForm(d, dismissibleCategories) {
  if (dismissibleCategories.has(d.fraud_category)) return true;
  return (d.yes_count ?? 0) >= 2;
}

function toggleLlmFeedbackForm(btn) {
  const form = btn.nextElementSibling;
  if (!form) return;
  form.classList.toggle("hidden");
  btn.textContent = form.classList.contains("hidden") ? "Fehlalarm melden" : "Abbrechen";
}

async function submitLlmFeedback(btn) {
  if (!lastReport) return;

  const section = btn.closest(".llm-feedback-section");
  const textarea = section?.querySelector(".llm-feedback-input");
  const feedbackText = textarea?.value?.trim() || "";

  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Wird gespeichert…";

  try {
    const resp = await fetch("/api/llm-feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: lastReport.url,
        domain: lastReport.domain,
        feedback_text: feedbackText,
        checks: lastReport.checks,
        previous_scan: lastReport.previous_scan || null,
      }),
    });

    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }

    const report = await resp.json();
    lastReport = report;
    completedChecks = report.checks;
    allChecksComplete = true;
    finalizeScore(report);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = originalText;
    alert("Feedback konnte nicht gespeichert werden. Bitte erneut versuchen.");
  }
}

// ── Helpers ──────────────────────────────────────────────────

function extractDomain(url) {
  try {
    const u = new URL(url.startsWith("http") ? url : "https://" + url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function toggleDetails(btn) {
  const open = btn.nextElementSibling.classList.toggle("open");
  btn.textContent = open ? "Details ausblenden ▴" : "Details anzeigen ▾";
}

function statusIcon(status) {
  return { passed: "✅", warning: "⚠️", failed: "❌", skipped: "⏭️", na: "➖", error: "⚡", loading: "⏳" }[status] || "❓";
}

function cardColor(status) {
  if (status === "passed") return "green";
  if (status === "warning") return "yellow";
  if (status === "failed" || status === "error") return "red";
  return "gray";
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setProgress(pct) {
  document.getElementById("progress-bar").style.width = pct + "%";
}

function setSubmitLoading(loading) {
  document.getElementById("submitText").classList.toggle("hidden", loading);
  document.getElementById("submitLoader").classList.toggle("hidden", !loading);
}

function copyReport() {
  if (!lastReport) return;
  const lines = [
    `Lynx Report`,
    `URL: ${lastReport.url}`,
    `Score: ${lastReport.total_score}/100 — ${lastReport.verdict}`,
    ``,
  ];
  if (lastReport.critical_flags?.length) {
    lines.push("⚠️ Critical Flags:");
    lastReport.critical_flags.forEach(f => lines.push(`  • ${f}`));
    lines.push("");
  }
  lines.push("Checks:");
  for (const check of lastReport.checks) {
    const sym = { passed: "✅", warning: "⚠️", failed: "❌", skipped: "–", na: "–", error: "!" }[check.status] || "?";
    lines.push(`  ${sym} ${check.display_name}: ${check.summary} (+${check.score}/${check.max_score} pts)`);
  }
  lines.push(`\nErstellt mit Lynx`);

  navigator.clipboard.writeText(lines.join("\n")).then(() => {
    const btn = document.getElementById("copyBtn");
    const orig = btn.textContent;
    btn.textContent = "✅ Copied!";
    setTimeout(() => { btn.textContent = orig; }, 2000);
  });
}

async function exportPdf() {
  if (!lastReport) return;
  const btn = document.getElementById("pdfBtn");
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Erstelle PDF…";
  try {
    const resp = await fetch("/api/report/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastReport),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `pruefbericht-${lastReport.domain}.pdf`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    alert("PDF-Export fehlgeschlagen.");
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}
