/** Compliance queue — reported CompanyCases. */

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  return detail ? String(detail) : "";
}

async function loadQueue() {
  const resp = await fetch("/api/compliance/reported-cases");
  const data = await resp.json();
  const cases = data.cases || [];
  document.getElementById("queueCount").textContent = String(cases.length);
  const el = document.getElementById("queueList");
  if (!cases.length) {
    el.innerHTML = `
      <div class="fraud-empty case-empty">
        <p><strong>Keine gemeldeten Fälle in der Queue.</strong></p>
        <p class="fraud-help">Fälle erscheinen hier, sobald ein Analyst den Report erzeugt hat.</p>
      </div>`;
    return;
  }
  el.innerHTML = `<ul class="fraud-side-list">${cases.map((c) => `
    <li>
      <div class="fraud-side-item-title">${esc(c.company_name)}
        <span class="fraud-speed-hint">${esc(c.fraud_type || "")}</span>
      </div>
      <div class="fraud-entry-meta">
        <span>Report: ${esc(c.reported_by || "")} · ${esc(c.reported_at || "")}</span>
        <span>${c.company_uid ? `<code>${esc(c.company_uid)}</code>` : ""}</span>
        ${c.hit_amount != null ? `<span>${esc(String(c.hit_amount))} ${esc(c.hit_currency || "CHF")}</span>` : ""}
      </div>
      <div class="fraud-side-links">
        <a class="btn-nav" href="/cases/${c.id}">Akte</a>
        ${c.has_report
          ? `<a class="btn-nav" href="/api/company-cases/${c.id}/report" target="_blank" rel="noopener">Report herunterladen</a>`
          : `<span class="fraud-help">Kein Report</span>`}
      </div>
      <div class="watch-status-row">
        <input class="watch-reason" data-note="${c.id}" placeholder="Compliance-Notiz (Pflicht)" />
        <button type="button" class="btn-case-equal btn-case-confirm" style="width:auto;min-width:8rem" data-action="${c.id}">Actioned</button>
      </div>
    </li>
  `).join("")}</ul>`;

  el.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.action;
      const note = el.querySelector(`[data-note="${id}"]`)?.value?.trim() || "";
      const r = await fetch(`/api/company-cases/${id}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ compliance_note: note }),
      });
      const d = await r.json();
      if (!r.ok) {
        document.getElementById("queueMsg").textContent = formatDetail(d.detail) || "Fehler";
        return;
      }
      document.getElementById("queueMsg").textContent = `Fall #${id} actioned / geschlossen`;
      loadQueue();
    });
  });
}

document.getElementById("refreshQueueBtn")?.addEventListener("click", loadQueue);
loadQueue();
