/** Team-wide CompanyCase list. */

const STATUS_LABELS = {
  under_review: "In Prüfung",
  confirmed_fraud: "Betrug bestätigt",
  ready_for_report: "Report bereit",
  reported: "Gemeldet",
  closed: "Fraudfall aktiv",
  cleared: "Kein Betrug",
};

/** Soft reminder: under_review older than this many calendar days gets a badge. */
const STALE_REVIEW_DAYS = 3;

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

function staleReviewBadge(c) {
  if (c.status !== "under_review") return "";
  const days = daysOpen(c.opened_at);
  if (days < STALE_REVIEW_DAYS) return "";
  return `<span class="case-stale-badge" title="Noch keine Bestätigung — bitte abklären oder schliessen">Offen seit ${days} Tagen</span>`;
}

async function loadCases() {
  const status = document.getElementById("statusFilter")?.value || "";
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const resp = await fetch(`/api/company-cases${qs}`);
  const data = await resp.json();
  const cases = data.cases || [];
  const el = document.getElementById("casesList");
  if (!cases.length) {
    el.innerHTML = `
      <div class="fraud-empty case-empty">
        <p><strong>Keine Fälle in diesem Filter.</strong></p>
        <p class="fraud-help">Neue Akte: Firma in der <a href="/">Firmenanalyse</a> suchen → «Akte eröffnen — In Prüfung».</p>
        <p class="fraud-help">Oder Filter auf «Alle» stellen.</p>
      </div>`;
    return;
  }
  el.innerHTML = `<ul class="fraud-side-list">${cases.map((c) => `
    <li class="watch-case-card${c.status === "under_review" && daysOpen(c.opened_at) >= STALE_REVIEW_DAYS ? " is-stale-review" : ""}">
      <div class="watch-case-card-row">
        <a class="watch-person-summary" href="/cases/${c.id}">
          <span class="fraud-side-item-title">${esc(typeof anon === "function" ? anon(c.company_name, "company") : c.company_name)}
            <span class="fraud-speed-hint">${esc(STATUS_LABELS[c.status] || c.status)}</span>
            ${staleReviewBadge(c)}
          </span>
          <span class="fraud-entry-meta">
            ${c.company_uid ? `<code>${esc(typeof anon === "function" ? anon(c.company_uid, "uid") : c.company_uid)}</code>` : ""}
            <span>von ${esc(typeof anon === "function" ? anon(c.opened_by, "user") : c.opened_by)} · ${formatDateDisplay(c.opened_at)}</span>
            ${c.fraud_type ? `<span>${esc(c.fraud_type)}</span>` : ""}
            ${c.status === "confirmed_fraud" || c.status === "ready_for_report"
              ? `<span>Checkliste: ${c.bank_checks_done}/${c.bank_checks_total}</span>`
              : ""}
          </span>
        </a>
        <button type="button" class="btn-nav" data-delete-case="${c.id}" title="Firmenakte löschen">Löschen</button>
      </div>
    </li>
  `).join("")}</ul>`;

  el.querySelectorAll("[data-delete-case]").forEach((btn) => {
    btn.addEventListener("click", async () => {
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

document.getElementById("statusFilter")?.addEventListener("change", loadCases);
loadCases();
