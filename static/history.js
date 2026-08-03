const CHECK_LABELS = {
  whois: "Domain Age",
  ssl: "SSL Certificate",
  dns: "DNS Records",
  hosting: "Hosting Location",
  wayback: "Web Archive",
  crt: "Certificate History",
  hsts: "HSTS Security",
  contact: "Contact Information",
  trustpilot: "Trustpilot",
  google_reviews: "Google Reviews",
  social_media: "Social Media",
  virustotal: "VirusTotal",
  safebrowsing: "Google Safe Browsing",
  urlscan: "URLScan.io",
  zefix: "Swiss Company (Zefix)",
  finma: "FINMA-Warnliste",
  iscan: "I-SCAN (IOSCO)",
  llm_content: "AI Fraud Analysis",
};

const VERDICT_COLORS = {
  "Likely Legitimate": "green",
  "Use Caution": "yellow",
  "High Risk": "orange",
  "Likely Fraudulent": "red",
  "Critical Risk": "red",
};

let currentOffset = 0;
let currentHasMore = false;
let searchTimeout = null;
let expandedScanId = null;

document.getElementById("domainSearch").addEventListener("input", () => {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    currentOffset = 0;
    loadHistory(false);
  }, 300);
});

document.getElementById("verdictFilter").addEventListener("change", () => {
  currentOffset = 0;
  loadHistory(false);
});

document.getElementById("loadMoreBtn").addEventListener("click", () => {
  if (currentHasMore) loadHistory(true);
});

loadHistory(false);

async function loadHistory(append) {
  const limit = 50;
  const offset = append ? currentOffset : 0;
  const domainSearch = document.getElementById("domainSearch").value.trim();
  const verdictFilter = document.getElementById("verdictFilter").value;

  const params = new URLSearchParams({ limit, offset });
  if (domainSearch) params.set("domain_search", domainSearch);
  if (verdictFilter) params.set("verdict_filter", verdictFilter);

  try {
    const resp = await fetch(`/api/history?${params}`);
    const data = await resp.json();

    renderStats(data.stats);
    renderTable(data.scans, append);

    currentOffset = offset + data.scans.length;
    currentHasMore = data.pagination.has_more;

    const btn = document.getElementById("loadMoreBtn");
    btn.classList.toggle("hidden", !currentHasMore);
    btn.textContent = `Nächste ${limit} laden`;
  } catch (e) {
    document.getElementById("historyTableBody").innerHTML =
      `<tr><td colspan="5" style="color:var(--c-red);padding:1.5rem;">Fehler beim Laden: ${escHtml(e.message)}</td></tr>`;
  }
}

function renderStats(stats) {
  const section = document.getElementById("statsSection");
  const verdictEntries = Object.entries(stats.verdict_distribution || {})
    .sort((a, b) => b[1] - a[1]);
  const maxCount = verdictEntries.length ? Math.max(...verdictEntries.map(([, c]) => c)) : 1;

  const verdictBars = verdictEntries.map(([verdict, count]) => {
    const color = VERDICT_COLORS[verdict] || "gray";
    const width = Math.round((count / maxCount) * 100);
    return `
      <div class="verdict-bar-row">
        <span class="verdict-bar-label" title="${escHtml(verdict)}">${escHtml(translateVerdict(verdict))}</span>
        <div class="verdict-bar-track">
          <div class="verdict-bar-fill bar-${color}" style="width:${width}%"></div>
        </div>
        <span>${count}</span>
      </div>`;
  }).join("");

  const topDomains = (stats.top_domains || [])
    .map((d) => `<li>${escHtml(d.domain)} <strong>(${d.count}×)</strong></li>`)
    .join("");

  section.innerHTML = `
    <div class="stat-card">
      <div class="stat-value">${stats.total_scans || 0}</div>
      <div class="stat-label">Prüfungen gesamt</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${stats.average_score || 0}</div>
      <div class="stat-label">Durchschnittsscore</div>
    </div>
    <div class="stat-card" style="grid-column: span 2;">
      <div class="stat-label">Verdict-Verteilung</div>
      <div class="verdict-bars">${verdictBars || "<span style='color:var(--c-muted)'>Noch keine Daten</span>"}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Häufigste Domains</div>
      <ul class="top-domains-list">${topDomains || "<li>Noch keine Daten</li>"}</ul>
    </div>`;
}

function renderTable(scans, append) {
  const tbody = document.getElementById("historyTableBody");

  if (!append) {
    tbody.innerHTML = "";
    expandedScanId = null;
  }

  if (!scans.length && !append) {
    tbody.innerHTML = `<tr><td colspan="7" style="color:var(--c-muted);padding:1.5rem;">Keine Prüfungen gefunden.</td></tr>`;
    return;
  }

  for (const scan of scans) {
    const color = VERDICT_COLORS[scan.verdict] || "gray";
    const date = scan.checked_at ? formatDateTimeDisplay(scan.checked_at) : "–";
    const company = scan.company_name || "–";
    const amount = scan.transaction_amount != null
      ? Number(scan.transaction_amount).toLocaleString("de-CH", { maximumFractionDigits: 2 })
      : "–";
    const currency = scan.transaction_currency || "–";

    const row = document.createElement("tr");
    row.className = "history-row";
    row.dataset.scanId = scan.id;
    row.innerHTML = `
      <td class="history-domain"><strong>${escHtml(scan.domain)}</strong></td>
      <td>${scan.total_score}/100</td>
      <td><span class="verdict-pill verdict-pill-${color}">${escHtml(translateVerdict(scan.verdict))}</span></td>
      <td>${escHtml(date)}</td>
      <td class="expert-only">${escHtml(amount)}</td>
      <td class="expert-only">${escHtml(currency)}</td>
      <td class="expert-only">${escHtml(company)}</td>`;

    row.addEventListener("click", () => toggleScanDetail(scan, row));
    tbody.appendChild(row);

    const detailRow = document.createElement("tr");
    detailRow.className = "history-detail-row hidden";
    detailRow.dataset.detailFor = scan.id;
    detailRow.innerHTML = `<td colspan="7"><div class="history-detail-panel"></div></td>`;
    tbody.appendChild(detailRow);
  }
}

function toggleScanDetail(scan, row) {
  const detailRow = row.nextElementSibling;
  const panel = detailRow.querySelector(".history-detail-panel");

  if (expandedScanId === scan.id) {
    detailRow.classList.add("hidden");
    expandedScanId = null;
    return;
  }

  document.querySelectorAll(".history-detail-row").forEach((el) => el.classList.add("hidden"));
  expandedScanId = scan.id;

  const checks = (scan.checks || []).map((check) => {
    const label = CHECK_LABELS[check.check_name] || check.check_name;
    const badge = check.status === "skipped" || check.status === "na"
      ? check.status.toUpperCase()
      : `+${check.score}/${check.max_score}`;
    return `
      <div class="history-check-item">
        <div class="history-check-item-header">
          <span>${escHtml(label)}</span>
          <span>${escHtml(badge)}</span>
        </div>
        <div class="history-check-item-summary">${escHtml(check.summary)}</div>
      </div>`;
  }).join("");

  panel.innerHTML = `
    <div style="margin-bottom:0.75rem;font-size:0.85rem;color:var(--c-muted);">
      URL: ${escHtml(scan.url)}${
        scan.transaction_amount != null
          ? ` · Transaktion: ${escHtml(Number(scan.transaction_amount).toLocaleString("de-CH"))} ${escHtml(scan.transaction_currency || "CHF")}${
              scan.transaction_purpose ? ` («${escHtml(scan.transaction_purpose)}»)` : ""
            }`
          : ""
      }${scan.critical_flags?.length ? ` · ⚠️ ${escHtml(scan.critical_flags.join("; "))}` : ""}
    </div>
    <div class="history-check-list">${checks || "<span>Keine Check-Details gespeichert.</span>"}</div>`;

  detailRow.classList.remove("hidden");
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
