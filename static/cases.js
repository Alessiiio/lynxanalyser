/** Team-wide CompanyCase list. */

const STATUS_LABELS = {
  under_review: "In Prüfung",
  confirmed_fraud: "Betrug bestätigt",
  ready_for_report: "Dokumentation fertig",
  reported: "Gemeldet",
  closed: "Fraudfall aktiv",
  cleared: "Kein Betrug",
};

/** Soft reminder: under_review older than this many calendar days gets a badge. */
const STALE_REVIEW_DAYS = 3;

let fraudTypeChoices = [];

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function daysOpen(iso) {
  if (!iso) return 0;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 0;
  return Math.floor((Date.now() - t) / 86400000);
}

function isStaleReview(c) {
  return c.status === "under_review" && daysOpen(c.opened_at) >= STALE_REVIEW_DAYS;
}

function statusBadgeClass(status) {
  if (status === "under_review") return "cases-badge--review";
  if (status === "cleared") return "cases-badge--cleared";
  if (status === "closed" || status === "reported") return "cases-badge--active";
  if (status === "confirmed_fraud" || status === "ready_for_report") return "cases-badge--confirmed";
  return "";
}

function sortCases(cases) {
  const rank = (c) => {
    if (c.status === "under_review" && isStaleReview(c)) return 0;
    if (c.status === "under_review") return 1;
    if (c.status === "confirmed_fraud" || c.status === "ready_for_report") return 2;
    if (c.status === "closed" || c.status === "reported") return 3;
    return 4;
  };
  return [...cases].sort((a, b) => {
    const d = rank(a) - rank(b);
    if (d !== 0) return d;
    return Date.parse(b.opened_at || 0) - Date.parse(a.opened_at || 0);
  });
}

function fillFraudTypeFilter(choices) {
  const sel = document.getElementById("fraudTypeFilter");
  if (!sel) return;
  const current = sel.value;
  const opts = [`<option value="">Alle Betrugsarten</option>`].concat(
    (choices || []).map(
      (t) => `<option value="${esc(t.value)}">${esc(t.label)}</option>`
    )
  );
  sel.innerHTML = opts.join("");
  if (current) sel.value = current;
}

function caseCard(c) {
  const name = typeof anon === "function" ? anon(c.company_name, "company") : c.company_name;
  const uid = c.company_uid
    ? typeof anon === "function"
      ? anon(c.company_uid, "uid")
      : c.company_uid
    : "";
  const opener = typeof anon === "function" ? anon(c.opened_by, "user") : c.opened_by;
  const statusLabel = STATUS_LABELS[c.status] || c.status;
  const typeLabel = c.fraud_type_label || "";
  const stale = isStaleReview(c);
  const days = daysOpen(c.opened_at);
  const showChecks =
    c.status === "confirmed_fraud" || c.status === "ready_for_report";

  return `
    <li class="cases-card${stale ? " is-stale-review" : ""}">
      <a class="cases-card-main" href="/cases/${c.id}">
        <div class="cases-card-top">
          <strong class="cases-card-title">${esc(name)}</strong>
          <span class="cases-badge ${statusBadgeClass(c.status)}">${esc(statusLabel)}</span>
          ${typeLabel ? `<span class="cases-badge cases-badge--type">${esc(typeLabel)}</span>` : ""}
          ${stale ? `<span class="case-stale-badge">Offen seit ${days} Tagen</span>` : ""}
        </div>
        <div class="cases-card-meta">
          ${uid ? `<code>${esc(uid)}</code>` : ""}
          <span>von ${esc(opener)} · ${formatDateDisplay(c.opened_at)}</span>
          ${
            showChecks
              ? `<span>Checkliste ${c.bank_checks_done || 0}/${c.bank_checks_total || 0}</span>`
              : ""
          }
        </div>
      </a>
      <button type="button" class="btn-nav cases-card-delete" data-delete-case="${c.id}" title="Firmenakte löschen">Löschen</button>
    </li>`;
}

async function loadCases() {
  const status = document.getElementById("statusFilter")?.value || "";
  const fraudType = document.getElementById("fraudTypeFilter")?.value || "";
  const q = document.getElementById("casesSearch")?.value.trim() || "";
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (fraudType) params.set("fraud_type", fraudType);
  if (q) params.set("q", q);
  const qs = params.toString() ? `?${params}` : "";
  const resp = await fetch(`/api/company-cases${qs}`);
  const data = await resp.json();
  const cases = sortCases(data.cases || []);
  if (Array.isArray(data.fraud_types) && data.fraud_types.length) {
    fraudTypeChoices = data.fraud_types;
    fillFraudTypeFilter(fraudTypeChoices);
  }
  const countEl = document.getElementById("casesCount");
  if (countEl) countEl.textContent = String(cases.length);
  const el = document.getElementById("casesList");
  if (!cases.length) {
    el.innerHTML = `
      <div class="fraud-empty case-empty">
        <p><strong>Keine Fälle in diesem Filter.</strong></p>
        <p class="fraud-help">Neue Akte: Firma in der <a href="/">Firmenanalyse</a> suchen → «Akte eröffnen».</p>
        <p class="fraud-help">Oder Filter zurücksetzen.</p>
      </div>`;
    return;
  }
  el.innerHTML = `<ul class="cases-list">${cases.map(caseCard).join("")}</ul>`;

  el.querySelectorAll("[data-delete-case]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const id = btn.dataset.deleteCase;
      if (!confirm(`Firmenakte #${id} unwiderruflich löschen?`)) return;
      const r = await fetch(`/api/company-cases/${id}`, { method: "DELETE" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        document.getElementById("casesMsg").textContent = d.detail || "Löschen fehlgeschlagen";
        return;
      }
      document.getElementById("casesMsg").textContent = `Fall #${id} gelöscht`;
      loadCases();
    });
  });
}

let searchTimer = null;
document.getElementById("statusFilter")?.addEventListener("change", loadCases);
document.getElementById("fraudTypeFilter")?.addEventListener("change", loadCases);
document.getElementById("casesSearch")?.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadCases, 250);
});
loadCases();
