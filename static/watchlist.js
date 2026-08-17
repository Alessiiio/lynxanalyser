/** Watchlist: triage list + modal Case/Personenakte. */

const STATUSES = [
  { value: "active", label: "Aktiv" },
  { value: "low_priority", label: "Niedrige Prio" },
  { value: "cleared", label: "Cleared" },
  { value: "confirmed_fraud", label: "Confirmed Fraud" },
];

const STATUS_LABEL = Object.fromEntries(STATUSES.map((s) => [s.value, s.label]));

let shabProgressTimer = null;
let shabProgressValue = 0;

function d(value, kind) {
  return typeof anon === "function" ? anon(value, kind) : value;
}

const PAGE_SIZE = 50;
let personOffset = 0;
let personTotal = 0;
let alertSeverityFilter = "";
let inboxAllItems = [];
let inboxTotal = 0;
let selectedInboxId = null;
let inboxSearchQuery = "";
let selectedPersonId = null;
let mergeSelected = new Set();
let companySelected = new Set();
let currentDossier = null;
let currentUserRole = "";
let bulkPollTimer = null;
let bulkJobId = null;
let bulkJobCache = null;
let bulkReviewIndex = 0;
let bulkPicks = new Map();
let bulkHydrated = new Set();
let bulkGraphNetwork = null;

async function loadMe() {
  const resp = await fetch("/api/me");
  if (!resp.ok) return;
  const data = await resp.json();
  const el = document.getElementById("watchUser");
  if (el && data.user) el.textContent = `${d(data.user.display_name, "user")} (${data.user.role})`;
  currentUserRole = (data.user && data.user.role) || "";
  const bulkBtn = document.getElementById("bulkTabBtn");
  if (bulkBtn && currentUserRole === "admin") bulkBtn.classList.remove("hidden");
  const highPrioBtn = document.getElementById("runHighPriorityBtn");
  if (highPrioBtn && currentUserRole === "admin") highPrioBtn.classList.remove("hidden");
  const cacheBtn = document.getElementById("refreshCompanyCacheBtn");
  if (cacheBtn && currentUserRole === "admin") cacheBtn.classList.remove("hidden");
  if (data.settings && typeof applyAnonymizeMode === "function") {
    applyAnonymizeMode(!!data.settings.anonymize_mode, { silent: true });
  }
}

function switchTab(name) {
  document.querySelectorAll(".watch-tabs .ca-tab").forEach((t) => {
    t.classList.toggle("is-active", t.dataset.tab === name);
  });
  document.getElementById("tabInbox")?.classList.toggle("is-active", name === "inbox");
  document.getElementById("tabCompanies")?.classList.toggle("is-active", name === "companies");
  document.getElementById("tabPersons")?.classList.toggle("is-active", name === "persons");
  document.getElementById("tabCases")?.classList.toggle("is-active", name === "cases");
  document.getElementById("tabBulk")?.classList.toggle("is-active", name === "bulk");
  if (name === "persons") loadPersons();
  if (name === "companies") loadCompanies();
  if (name === "cases") loadCases();
  if (name === "inbox") loadInbox();
}

function openPersonFromInbox(personId) {
  if (!personId) return;
  selectedPersonId = Number(personId);
  switchTab("persons");
  openCaseModal(selectedPersonId);
}

function closeCaseModal() {
  document.getElementById("caseModal")?.classList.add("hidden");
  document.body.classList.remove("watch-modal-open");
}

async function openCaseModal(personId, { autoScan = false } = {}) {
  selectedPersonId = Number(personId);
  const modal = document.getElementById("caseModal");
  const body = document.getElementById("caseModalBody");
  const title = document.getElementById("caseModalTitle");
  const meta = document.getElementById("caseModalMeta");
  stopShabProgress(true);
  modal.classList.remove("hidden");
  document.body.classList.add("watch-modal-open");
  title.textContent = "Lade Akte…";
  meta.textContent = "";
  setCaseStatus("");
  body.innerHTML = `<p class="fraud-help">Lade Registerdaten…</p>`;

  const resp = await fetch(`/api/watched-persons/${personId}`);
  if (!resp.ok) {
    body.innerHTML = `<p class="fraud-help">Akte nicht ladbar.</p>`;
    return;
  }
  const p = await resp.json();
  currentDossier = p;
  renderCaseModal(p);

  // Kein Auto-Scan — nur manuell per Button.
  if (p.seed_only || (p.companies || []).length <= 1) {
    setCaseStatus(
      "Bisher nur Seed-Firma. «Mandate suchen» nutzt Moneyhouse (Person) + Zefix (Firma)."
    );
  }
}

function renderCaseModal(p) {
  const title = document.getElementById("caseModalTitle");
  const meta = document.getElementById("caseModalMeta");
  const body = document.getElementById("caseModalBody");
  title.textContent = d(p.display_name, "person") || "—";
  const sourceUrl = String(p.source_company_url || "");
  const sourceSafe = /^https?:\/\//i.test(sourceUrl) ? sourceUrl : "";
  const sourceLink = sourceSafe
    ? `<a href="${esc(sourceSafe)}" target="_blank" rel="noopener">${esc(d(p.source_company_name, "company"))}</a>`
    : esc(d(p.source_company_name || "—", "company"));
  const statusLabel = STATUS_LABEL[p.status] || p.status;
  const caseHint = p.has_company_case && p.linked_case_id
    ? `<a class="btn-nav" href="/cases/${p.linked_case_id}">Zum Fraudfall #${p.linked_case_id}</a>`
    : `<span class="watch-no-case-badge">Ohne Fraudfall</span>
       <span class="fraud-help">Frühwarnung — bei Verdacht in der Firmenanalyse eine Akte eröffnen.</span>`;
  meta.innerHTML = `
    <span class="watch-meta-pill watch-meta-pill--${esc(p.status || "active")}">${esc(statusLabel)}</span>
    ${p.residence ? `<span class="watch-meta-pill">${esc(p.residence)}</span>` : ""}
    ${p.source_reason ? `<span class="watch-meta-pill">${esc(p.source_reason)}</span>` : ""}
    <span class="watch-meta-pill">Ursprung: ${sourceLink}</span>
    <span class="watch-case-hint-row">${caseHint}</span>
  `;

  const companies = p.companies || [];
  const alerts = p.alerts || [];
  const hist = p.status_history || [];
  const onlySeed = p.seed_only || companies.every((c) => c.is_seed_company || c.relation_type === "seed");

  body.innerHTML = `
    <div class="watch-case-layout">
      <div class="watch-case-col watch-case-col--side">
        <section class="watch-dossier-card">
          <h3>Status &amp; Flags</h3>
          <div class="watch-status-row">
            <select id="dossierStatus" class="ca-select" aria-label="Status">
              ${STATUSES.map((s) =>
                `<option value="${s.value}" ${s.value === p.status ? "selected" : ""}>${s.label}</option>`
              ).join("")}
            </select>
            <input id="dossierReason" class="watch-reason" placeholder="Begründung (Pflicht)" />
            <button type="button" class="btn-nav" id="dossierSaveStatus">Speichern</button>
          </div>
          <div class="watch-flags-row">
            <label class="watch-flag-check">
              <input type="checkbox" id="flagUndesired" ${p.flag_undesired_customer ? "checked" : ""}>
              <span>Unerwünschter Kunde</span>
            </label>
            <label class="watch-flag-check">
              <input type="checkbox" id="flagAml" ${p.flag_aml ? "checked" : ""}>
              <span>AML</span>
            </label>
            <button type="button" class="btn-nav" id="dossierSaveFlags">Flags speichern</button>
          </div>
          ${hist.length ? `<ul class="watch-hist-list">${hist.slice(0, 4).map((h) =>
            `<li>${esc(formatDateTimeDisplay(h.changed_at))} · ${esc(h.old_status)} → ${esc(h.new_status)} · ${esc(h.changed_by)}</li>`
          ).join("")}</ul>` : ""}
        </section>

        <section class="watch-dossier-card">
          <h3>Fallnotiz</h3>
          <p class="fraud-help">Sachverhalt, Hypothesen, Verweise — keine Bankkundendaten im Klartext.</p>
          <textarea id="caseNotes" class="fraud-net-textarea" rows="5" placeholder="Was wissen wir? Was prüfen? Interne Referenzen…">${esc(p.case_notes || "")}</textarea>
          <div class="fraud-inline-actions" style="margin-top:0.5rem">
            <button type="button" class="btn-nav" id="saveCaseNotes">Notiz speichern</button>
          </div>
        </section>
      </div>

      <div class="watch-case-col watch-case-col--main">
        <section class="watch-dossier-card">
          <div class="watch-dossier-card-head">
            <h3>Firmenverbindungen <span class="fraud-badge">${companies.length}</span></h3>
          </div>
          ${onlySeed ? `<p class="watch-seed-hint">Nur Seed-Firma bisher. Mandate werden per Moneyhouse-Personensuche gefunden und über Zefix (UID/EHRAID) verknüpft.</p>` : ""}
          <div class="watch-scan-controls">
            <button type="button" class="btn-check" id="dossierScan">Mandate suchen</button>
            <label class="watch-flag-check" title="Optionaler SHAB-Nachscan (langsam)">
              <input type="checkbox" id="dossierScanShab">
              <span>+ SHAB</span>
            </label>
          </div>
          <p class="fraud-help watch-scan-hint">Personensuche: Moneyhouse → Firmenabgleich: Zefix. Die Firmensuche / Analyse bleibt auf Zefix.</p>
          ${companies.length ? `<div class="watch-table-wrap"><table class="watch-table">
            <thead><tr><th>Firma</th><th>Rolle</th><th>Herkunft</th><th>Seit</th><th></th></tr></thead>
            <tbody>${companies.map((c) => {
              const origin = c.is_seed_company || c.relation_type === "seed"
                ? "seed" : (c.relation_type === "newly_found" ? "newly_found" : (c.relation_type || "—"));
              const href = c.name ? `/?company=${encodeURIComponent(c.name)}` : null;
              return `<tr>
                <td>${esc(d(c.name, "company"))}${c.uid ? `<div class="fraud-help">${esc(d(c.uid, "uid"))}</div>` : ""}</td>
                <td>${esc(c.role || "—")}</td>
                <td>${esc(origin)}</td>
                <td>${esc(formatDateDisplay(c.first_detected_at))}</td>
                <td>${href ? `<a class="btn-nav" href="${href}">Analyse</a>` : ""}</td>
              </tr>`;
            }).join("")}</tbody>
          </table></div>` : `<p class="fraud-help">Keine Firmenverbindungen.</p>`}
        </section>

        <section class="watch-dossier-card">
          <h3>Fund-Historie <span class="fraud-badge">${alerts.length}</span></h3>
          ${alerts.length ? `<ul class="fraud-side-list">${alerts.map((a) => `
            <li>
              <div class="fraud-side-item-title">${esc(inboxSubject(a.alert_type))}
                ${a.acknowledged ? `<span class="fraud-speed-hint">erledigt</span>` : ""}
              </div>
              <div class="fraud-entry-meta">${esc(inboxPreview(a))}</div>
              <div class="fraud-side-links">
                ${!a.acknowledged ? `<button type="button" class="btn-nav" data-ack="${a.id}">Erledigt</button>` : ""}
                <button type="button" class="btn-check" data-to-case="${a.id}">Fall eröffnen</button>
              </div>
            </li>
          `).join("")}</ul>` : `<p class="fraud-help">Keine Funde.</p>`}
        </section>

        <div class="watch-case-footer">
          <a class="btn-check" id="downloadDossier" href="/api/watched-persons/${p.id}/investigation-report" target="_blank" rel="noopener">Ermittlungsdossier PDF</a>
        </div>
      </div>
    </div>
  `;

  wireCaseModalActions(p.id);
}

function wireCaseModalActions(personId) {
  document.getElementById("dossierSaveStatus")?.addEventListener("click", async () => {
    const status = document.getElementById("dossierStatus")?.value;
    const reason = document.getElementById("dossierReason")?.value?.trim() || "";
    if (reason.length < 3) {
      setMsg("Begründung ist Pflicht");
      return;
    }
    const r = await fetch(`/api/watched-persons/${personId}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, reason }),
    });
    const d = await r.json();
    if (!r.ok) {
      setMsg(formatDetail(d.detail) || "Fehler");
      return;
    }
    setCaseStatus(`Status → ${d.status}`);
    openCaseModal(personId, { autoScan: false });
    loadPersons();
  });

  document.getElementById("dossierSaveFlags")?.addEventListener("click", async () => {
    const flag_undesired_customer = !!document.getElementById("flagUndesired")?.checked;
    const flag_aml = !!document.getElementById("flagAml")?.checked;
    const r = await fetch(`/api/watched-persons/${personId}/flags`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flag_undesired_customer, flag_aml }),
    });
    const d = await r.json();
    if (!r.ok) {
      setCaseStatus(formatDetail(d.detail) || "Flags speichern fehlgeschlagen");
      return;
    }
    setCaseStatus("Flags gespeichert");
    loadPersons();
  });

  document.getElementById("dossierScan")?.addEventListener("click", () => runShabScan(personId));

  document.getElementById("saveCaseNotes")?.addEventListener("click", async () => {
    const notes = document.getElementById("caseNotes")?.value || "";
    const r = await fetch(`/api/watched-persons/${personId}/case-notes`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_notes: notes }),
    });
    if (!r.ok) {
      const d = await r.json();
      setCaseStatus(formatDetail(d.detail) || "Speichern fehlgeschlagen");
      return;
    }
    setCaseStatus("Fallnotiz gespeichert");
  });

  document.querySelectorAll("[data-ack]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/network-alerts/${btn.dataset.ack}/ack`, { method: "POST" });
      openCaseModal(personId, { autoScan: false });
      loadInbox();
    });
  });

  document.querySelectorAll("[data-to-case]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const alertId = btn.dataset.toCase;
      btn.disabled = true;
      const r = await fetch(`/api/company-cases/from-alert/${alertId}`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) {
        setCaseStatus(formatDetail(d.detail) || "Fall konnte nicht eröffnet werden");
        btn.disabled = false;
        return;
      }
      setCaseStatus(`Fall #${d.id} eröffnet (${d.already_existed ? "bereits vorhanden" : "neu"})`);
      if (d.id) location.href = `/cases/${d.id}`;
    });
  });
}

function startShabProgress({ nationwide = false } = {}) {
  stopShabProgress(false);
  shabProgressValue = 4;
  const feedback = document.getElementById("caseModalScanFeedback");
  const wrap = document.getElementById("caseScanProgressWrap");
  const bar = document.getElementById("caseScanProgressBar");
  feedback?.classList.remove("hidden");
  wrap?.classList.remove("hidden");
  wrap?.setAttribute("aria-hidden", "false");
  if (bar) bar.style.width = `${shabProgressValue}%`;

  const eta = nationwide ? 55000 : 8000;
  const tickMs = 280;
  const targetBeforeDone = 92;
  shabProgressTimer = setInterval(() => {
    const step = Math.max(0.35, (targetBeforeDone - shabProgressValue) * (tickMs / eta) * 1.4);
    shabProgressValue = Math.min(targetBeforeDone, shabProgressValue + step);
    if (bar) bar.style.width = `${shabProgressValue.toFixed(1)}%`;
  }, tickMs);
}

function finishShabProgress() {
  return new Promise((resolve) => {
    stopShabProgress(false);
    const wrap = document.getElementById("caseScanProgressWrap");
    const bar = document.getElementById("caseScanProgressBar");
    shabProgressValue = 100;
    if (bar) bar.style.width = "100%";
    setTimeout(() => {
      wrap?.classList.add("hidden");
      wrap?.setAttribute("aria-hidden", "true");
      if (bar) bar.style.width = "0%";
      shabProgressValue = 0;
      resolve();
    }, 320);
  });
}

function stopShabProgress(hide = true) {
  if (shabProgressTimer) {
    clearInterval(shabProgressTimer);
    shabProgressTimer = null;
  }
  if (hide) {
    const wrap = document.getElementById("caseScanProgressWrap");
    const bar = document.getElementById("caseScanProgressBar");
    const feedback = document.getElementById("caseModalScanFeedback");
    wrap?.classList.add("hidden");
    wrap?.setAttribute("aria-hidden", "true");
    if (bar) bar.style.width = "0%";
    if (feedback && !(document.getElementById("caseModalStatus")?.textContent || "").trim()) {
      feedback.classList.add("hidden");
    }
    shabProgressValue = 0;
  }
}

async function runShabScan(personId, { quiet = false } = {}) {
  const btn = document.getElementById("dossierScan");
  const includeShab = !!document.getElementById("dossierScanShab")?.checked;
  if (btn) btn.disabled = true;
  if (!quiet) {
    setCaseStatus(
      includeShab
        ? "Mandate: Moneyhouse + Zefix, danach optionaler SHAB-Nachscan…"
        : "Mandate suchen (Moneyhouse → Zefix-Abgleich)…"
    );
    startShabProgress({ nationwide: includeShab });
  }
  try {
    const qs = includeShab ? "?include_shab=1" : "";
    const r = await fetch(`/api/watched-persons/${personId}/scan${qs}`, { method: "POST" });
    const data = await r.json();
    await finishShabProgress();
    if (!r.ok) {
      setCaseStatus(formatDetail(data.detail) || data.error || "Scan fehlgeschlagen");
      return;
    }
    const mh = data.moneyhouse || {};
    const summary =
      `Fertig: ${data.new_links || 0} neue Firmen, ${data.alerts || 0} Alerts` +
      (mh.matched_person ? ` · Person: ${mh.matched_person}` : "") +
      (data.zefix_resolved != null ? ` · Zefix ${data.zefix_resolved}` : "") +
      (mh.companies_found != null ? ` · MH-Mandate ${mh.companies_found}` : "") +
      (data.zefix_failed && data.zefix_failed.length
        ? ` · ohne Zefix: ${data.zefix_failed.join(", ")}`
        : "");
    setCaseStatus(summary);
    await openCaseModal(personId, { autoScan: false });
    setCaseStatus(summary);
    loadPersons();
    loadInbox();
  } catch (e) {
    stopShabProgress(true);
    setCaseStatus(String(e.message || e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

function setCaseStatus(msg) {
  const el = document.getElementById("caseModalStatus");
  const feedback = document.getElementById("caseModalScanFeedback");
  if (el) el.textContent = msg || "";
  if (feedback) {
    if (msg) feedback.classList.remove("hidden");
    else if (!shabProgressTimer) feedback.classList.add("hidden");
  }
}

function inboxSubject(alertType) {
  switch (alertType) {
    case "new_company_founded":
      return "Neue Firma";
    case "new_role":
      return "Neue Funktion";
    case "organ_exit":
      return "Austritt";
    default:
      return "Neue Verbindung";
  }
}

function inboxRoleHint(message) {
  const m = String(message || "").match(/»\s*\(([^)]+)\)\s*(?:—|-|\[)/);
  const role = (m && m[1] ? m[1] : "").trim();
  if (!role || /^(entered|exited|shab)/i.test(role)) return "";
  return role;
}

function inboxPreview(a) {
  const company = d(a.company_name, "company");
  const role = inboxRoleHint(a.message);
  if (a.alert_type === "organ_exit") {
    return company ? `Nicht mehr bei ${company}` : "Austritt aus einer Firma";
  }
  if (role && company) return `${role} · ${company}`;
  return company || inboxSubject(a.alert_type);
}

function inboxBody(a) {
  const person = d(a.person_name, "person") || "Eine beobachtete Person";
  const company = d(a.company_name, "company") || "einer Firma";
  const role = inboxRoleHint(a.message);
  if (a.alert_type === "organ_exit") {
    return `${person} ist nicht mehr bei ${company} eingetragen.`;
  }
  if (role) {
    return `${person} ist neu als ${role} bei ${company} eingetragen.`;
  }
  if (a.alert_type === "new_company_founded") {
    return `${person} erscheint neu bei ${company}.`;
  }
  return `${person} ist neu mit ${company} verbunden.`;
}

function inboxInitials(name) {
  const parts = String(name || "")
    .split(/[\s,]+/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function inboxWhenList(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const date = new Date(t);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startThat = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.round((startToday - startThat) / 86400000);
  const hh = String(date.getHours()).padStart(2, "0");
  const mi = String(date.getMinutes()).padStart(2, "0");
  if (diffDays === 0) return `${hh}:${mi}`;
  if (diffDays === 1) return "Gestern";
  return formatDateDisplay(iso);
}

function inboxCountLabel(n) {
  if (n <= 0) return "Keine neuen";
  if (n === 1) return "1 neu";
  return `${n} neu`;
}

function getInboxFiltered() {
  let items = inboxAllItems;
  if (alertSeverityFilter) {
    items = items.filter((it) => it.payload && it.payload.severity === alertSeverityFilter);
  }
  const q = inboxSearchQuery.trim().toLowerCase();
  if (q) {
    items = items.filter((it) => {
      const a = it.payload || {};
      const hay = [
        a.person_name,
        a.company_name,
        a.source_company_name,
        inboxSubject(a.alert_type),
        inboxPreview(a),
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }
  return items;
}

function inboxEmptyMarkup(filtered) {
  if (inboxAllItems.length && filtered) {
    return `<div class="inbox-empty">
      <p class="inbox-empty-title">Keine Treffer</p>
      <p class="inbox-empty-copy">Suche oder Filter anpassen.</p>
    </div>`;
  }
  return `<div class="inbox-empty">
    <svg class="inbox-empty-icon" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
      <polyline points="22,6 12,13 2,6"/>
    </svg>
    <p class="inbox-empty-title">Keine neuen Meldungen</p>
    <p class="inbox-empty-copy">Neue Firmen und Funktionen beobachteter Personen erscheinen hier.</p>
  </div>`;
}

function renderInboxReading(a) {
  const el = document.getElementById("inboxRead");
  const shell = document.getElementById("inboxShell");
  if (!el) return;
  if (!a) {
    shell?.classList.remove("is-reading");
    el.innerHTML = `<div class="inbox-empty inbox-empty--pane">
      <p class="inbox-empty-title">Keine Meldung gewählt</p>
      <p class="inbox-empty-copy">Wähle links eine Meldung.</p>
    </div>`;
    return;
  }
  shell?.classList.add("is-reading");
  const person = d(a.person_name, "person") || "Unbekannt";
  const company = d(a.company_name, "company") || "—";
  const important = a.severity === "high";
  const context = a.source_company_name
    ? `Steht auf der Liste wegen ${esc(d(a.source_company_name, "company"))}.`
    : "";
  const href = a.company_name ? `/?company=${encodeURIComponent(a.company_name)}` : "";
  el.innerHTML = `
    <div class="inbox-letter">
      <button type="button" class="inbox-back btn-nav" id="inboxBackBtn">Zurück zur Liste</button>
      <div class="inbox-letter-kicker">
        ${important ? `<span class="inbox-flag">Wichtig</span>` : ""}
        <time class="inbox-letter-time" datetime="${esc(a.created_at || "")}">${esc(formatDateTimeDisplay(a.created_at))}</time>
      </div>
      <h3 class="inbox-letter-subject">${esc(inboxSubject(a.alert_type))}</h3>
      <dl class="inbox-letter-meta">
        <div><dt>Person</dt><dd>${esc(person)}</dd></div>
        <div><dt>Firma</dt><dd>${href ? `<a href="${esc(href)}">${esc(company)}</a>` : esc(company)}</dd></div>
      </dl>
      <p class="inbox-letter-body">${esc(inboxBody(a))}</p>
      ${context ? `<p class="inbox-letter-context">${context}</p>` : ""}
      <p id="inboxActionMsg" class="inbox-letter-msg" hidden></p>
      <div class="inbox-letter-actions">
        ${a.person_id ? `<button type="button" class="btn-nav" data-open-person="${a.person_id}">Akte öffnen</button>` : ""}
        <button type="button" class="btn-check" data-from-alert="${a.id}">Fall eröffnen</button>
        <button type="button" class="btn-nav" data-ack="${a.id}">Erledigt</button>
      </div>
    </div>`;
  bindInboxActions(el);
}

function bindInboxActions(root) {
  root.querySelector("#inboxBackBtn")?.addEventListener("click", () => {
    selectedInboxId = null;
    renderInbox();
  });
  root.querySelectorAll("[data-open-person]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openPersonFromInbox(btn.dataset.openPerson);
    });
  });
  root.querySelectorAll("[data-from-alert]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      const r = await fetch(`/api/company-cases/from-alert/${btn.dataset.fromAlert}`, { method: "POST" });
      const payload = await r.json().catch(() => ({}));
      if (!r.ok) {
        const msgEl = document.getElementById("inboxActionMsg");
        if (msgEl) {
          msgEl.hidden = false;
          msgEl.textContent = formatDetail(payload.detail) || "Fall konnte nicht eröffnet werden.";
        }
        btn.disabled = false;
        return;
      }
      if (payload.id) location.href = `/cases/${payload.id}`;
    });
  });
  root.querySelectorAll("[data-ack]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      const items = getInboxFiltered();
      const idx = items.findIndex((it) => it.payload && String(it.payload.id) === String(btn.dataset.ack));
      const next = items[idx + 1] || items[idx - 1];
      selectedInboxId = next && next.payload ? next.payload.id : null;
      await fetch(`/api/network-alerts/${btn.dataset.ack}/ack`, { method: "POST" });
      loadInbox();
    });
  });
}

function renderInbox() {
  const items = getInboxFiltered();
  const badge = document.getElementById("inboxBadge");
  const countEl = document.getElementById("inboxCountLabel");
  if (badge) badge.textContent = String(inboxTotal);
  if (countEl) countEl.textContent = inboxCountLabel(inboxTotal);

  const listEl = document.getElementById("inboxList");
  const readEl = document.getElementById("inboxRead");
  const shell = document.getElementById("inboxShell");
  if (!listEl) return;

  if (!items.length) {
    selectedInboxId = null;
    listEl.innerHTML = inboxEmptyMarkup(Boolean(inboxSearchQuery || alertSeverityFilter));
    if (readEl) {
      readEl.innerHTML = "";
    }
    shell?.classList.remove("is-reading");
    shell?.classList.toggle("is-empty", true);
    return;
  }
  shell?.classList.toggle("is-empty", false);

  const stillVisible = items.some((it) => it.payload && it.payload.id === selectedInboxId);
  if (!stillVisible) {
    const desktop = window.matchMedia("(min-width: 801px)").matches;
    selectedInboxId = desktop && items[0].payload ? items[0].payload.id : null;
  }

  listEl.innerHTML = items.map((it) => {
    const a = it.payload || {};
    const person = d(a.person_name, "person") || "Unbekannt";
    const selected = a.id === selectedInboxId;
    const important = a.severity === "high";
    return `<button type="button" class="inbox-row${selected ? " is-selected" : ""}${important ? " is-important" : ""}" role="option" aria-selected="${selected ? "true" : "false"}" data-inbox-id="${a.id}">
      <span class="inbox-avatar" aria-hidden="true">${esc(inboxInitials(a.person_name))}</span>
      <span class="inbox-row-main">
        <span class="inbox-row-top">
          <span class="inbox-row-from">${esc(person)}</span>
          <time class="inbox-row-when" datetime="${esc(a.created_at || "")}">${esc(inboxWhenList(a.created_at))}</time>
        </span>
        <span class="inbox-row-subject">${esc(inboxSubject(a.alert_type))}${important ? `<span class="inbox-flag inbox-flag--inline">Wichtig</span>` : ""}</span>
        <span class="inbox-row-preview">${esc(inboxPreview(a))}</span>
      </span>
    </button>`;
  }).join("");

  listEl.querySelectorAll("[data-inbox-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedInboxId = Number(btn.dataset.inboxId);
      renderInbox();
    });
  });
  const selectedRow = listEl.querySelector(".inbox-row.is-selected");
  if (selectedRow) {
    const top = selectedRow.offsetTop;
    const bottom = top + selectedRow.offsetHeight;
    if (top < listEl.scrollTop) listEl.scrollTop = top;
    else if (bottom > listEl.scrollTop + listEl.clientHeight) {
      listEl.scrollTop = bottom - listEl.clientHeight;
    }
  }

  const selected = items.find((it) => it.payload && it.payload.id === selectedInboxId);
  renderInboxReading(selected ? selected.payload : null);
}

function moveInboxSelection(delta) {
  const items = getInboxFiltered();
  if (!items.length) return;
  const idx = items.findIndex((it) => it.payload && it.payload.id === selectedInboxId);
  const next = Math.max(0, Math.min(items.length - 1, (idx < 0 ? 0 : idx) + delta));
  selectedInboxId = items[next].payload.id;
  renderInbox();
}

async function loadInbox() {
  const resp = await fetch("/api/watchlist/inbox?limit=150");
  const data = await resp.json();
  inboxAllItems = data.items || [];
  inboxTotal = data.total || inboxAllItems.length;
  renderInbox();
}

function personQueryParams() {
  const qs = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(personOffset),
    sort: document.getElementById("sortFilter")?.value || "priority",
  });
  const status = document.getElementById("statusFilter")?.value;
  if (status) qs.set("status", status);
  const q = document.getElementById("personSearch")?.value?.trim();
  if (q) qs.set("q", q);
  if (document.getElementById("openAlertOnly")?.checked) qs.set("has_open_alert", "true");
  return qs;
}

async function loadPersons() {
  const resp = await fetch(`/api/watched-persons?${personQueryParams()}`);
  const data = await resp.json();
  const persons = data.items || data.persons || [];
  personTotal = data.total || persons.length;
  document.getElementById("personCount").textContent = String(personTotal);
  const page = Math.floor(personOffset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(personTotal / PAGE_SIZE));
  document.getElementById("pageInfo").textContent = `${page}/${pages} · ${personTotal}`;
  document.getElementById("prevPageBtn").disabled = personOffset <= 0;
  document.getElementById("nextPageBtn").disabled = personOffset + PAGE_SIZE >= personTotal;

  const covEl = document.getElementById("scanCoverageHint");
  if (covEl) {
    const cov = data.coverage;
    const shab = data.shab_daily;
    const parts = [];
    if (cov && cov.hint) {
      parts.push(`${cov.hint} · Nacht-Cron rollt die Liste weiter (siehe Hilfe unten).`);
    }
    if (shab && shab.hint) {
      parts.push(shab.hint);
    }
    covEl.textContent = parts.join(" ");
  }

  const el = document.getElementById("personList");
  if (!persons.length) {
    el.innerHTML = `<p class="fraud-help">Keine Personen.</p>`;
    return;
  }
  el.innerHTML = `<ul class="fraud-side-list watch-person-list">${persons.map((p) => {
    const active = selectedPersonId === p.id ? " is-selected" : "";
    const inter = p.probable_intermediary
      ? `<span class="watch-inter-badge">Intermediär</span>` : "";
    const undesired = p.flag_undesired_customer
      ? `<span class="watch-flag-badge is-undesired" title="Unerwünschter Kunde">Unerwünscht</span>` : "";
    const aml = p.flag_aml
      ? `<span class="watch-flag-badge is-aml" title="AML">AML</span>` : "";
    const caseBadge = p.has_company_case
      ? `<span class="watch-case-link-badge" title="Registrierter Fraudfall">Fraudfall #${esc(String(p.linked_case_id || ""))}</span>`
      : `<span class="watch-no-case-badge" title="Frühwarnung ohne Fraudfall — Akte eröffnen empfohlen">Ohne Fraudfall</span>`;
    const scanPrio = (p.scan_priority || "") === "high"
      ? `<span class="watch-case-link-badge" title="Nächtlich zuerst (Fall / In Abklärung)">High-Scan</span>`
      : "";
    const lastScan = p.last_monitored_at
      ? formatScanAge(p.last_monitored_at)
      : "nie gescannt";
    return `<li class="watch-person-row${active}${p.probable_intermediary ? " is-intermediary-collapsed" : ""}${p.has_company_case ? "" : " is-no-case"}">
      <button type="button" class="watch-person-summary" data-select="${p.id}">
        <span class="fraud-side-item-title">${esc(d(p.display_name, "person"))}
          <span class="fraud-speed-hint">${esc(p.status)}</span>${inter}${undesired}${aml}${caseBadge}${scanPrio}
        </span>
        <span class="fraud-entry-meta">
          <span>${p.company_count || 0} Firmen</span>
          <span>${p.open_alert_count || 0} Alerts</span>
          <span>Prio ${p.priority_score ?? "—"}</span>
          <span title="Letzter Monitoring-Scan">${esc(lastScan)}</span>
        </span>
      </button>
      <label class="watch-merge-label">
        <input type="checkbox" data-merge-id="${p.id}" ${mergeSelected.has(p.id) ? "checked" : ""} />
      </label>
    </li>`;
  }).join("")}</ul>`;

  el.querySelectorAll("[data-select]").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedPersonId = Number(btn.dataset.select);
      el.querySelectorAll(".watch-person-row").forEach((row) => {
        row.classList.toggle("is-selected", Number(row.querySelector("[data-select]")?.dataset.select) === selectedPersonId);
      });
      openCaseModal(selectedPersonId);
    });
  });
  el.querySelectorAll("[data-merge-id]").forEach((cb) => {
    cb.addEventListener("change", () => {
      const id = Number(cb.dataset.mergeId);
      if (cb.checked) mergeSelected.add(id);
      else mergeSelected.delete(id);
    });
  });
}

function formatScanAge(iso) {
  if (!iso) return "nie gescannt";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "Scan ?";
  const days = Math.floor((Date.now() - t) / 86400000);
  if (days <= 0) return "heute gescannt";
  if (days === 1) return "gestern gescannt";
  if (days < 14) return `vor ${days} Tagen`;
  return `Scan ${new Date(t).toLocaleDateString("de-CH")}`;
}

async function loadCases() {
  const resp = await fetch("/api/watched-persons/cases?limit=100");
  const data = await resp.json();
  const cases = data.cases || [];
  document.getElementById("caseCount").textContent = String(data.total || cases.length);
  const el = document.getElementById("caseList");
  if (!cases.length) {
    el.innerHTML = `<p class="fraud-help">Keine Fälle.</p>`;
    return;
  }
  el.innerHTML = `<ul class="fraud-side-list">${cases.map((c, idx) => {
    const personIds = (c.persons || []).map((p) => p.id).filter(Boolean);
    return `
    <li class="watch-case-card">
      <div class="watch-case-card-row">
        <button type="button" class="watch-person-summary" data-case-toggle="${idx}">
          <span class="fraud-side-item-title">${esc(d(c.source_company_name || "Ohne Ursprungsfirma", "company"))}</span>
          <span class="fraud-entry-meta">
            <span>${c.person_count} Personen</span>
            <span>${c.open_alerts} Alerts</span>
          </span>
        </button>
        <button type="button" class="btn-nav" data-delete-case-persons="${esc(personIds.join(","))}"
          title="Alle Personen dieses Falls löschen">Löschen</button>
      </div>
      <div class="watch-case-persons hidden" id="case-body-${idx}">
        <ul class="fraud-side-list">${(c.persons || []).map((p) =>
          `<li>
            <button type="button" class="watch-person-summary" data-open-person="${p.id}">
              <span class="fraud-side-item-title">${esc(d(p.display_name, "person"))}
                <span class="fraud-speed-hint">${esc(p.status)}</span>
              </span>
            </button>
          </li>`
        ).join("")}</ul>
      </div>
    </li>`;
  }).join("")}</ul>`;
  el.querySelectorAll("[data-case-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(`case-body-${btn.dataset.caseToggle}`)?.classList.toggle("hidden");
    });
  });
  el.querySelectorAll("[data-open-person]").forEach((btn) => {
    btn.addEventListener("click", () => openPersonFromInbox(btn.dataset.openPerson));
  });
  el.querySelectorAll("[data-delete-case-persons]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const ids = String(btn.dataset.deleteCasePersons || "")
        .split(",")
        .map((x) => Number(x))
        .filter((n) => n > 0);
      if (!ids.length) return;
      if (!confirm(`${ids.length} Person(en) dieses Falls unwiderruflich von der Watchlist löschen?`)) return;
      const r = await fetch("/api/watched-persons/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      const d = await r.json();
      if (!r.ok) {
        setMsg(formatDetail(d.detail) || "Löschen fehlgeschlagen");
        return;
      }
      setMsg(`${d.deleted_count || ids.length} Person(en) gelöscht`);
      loadCases();
      loadPersons();
      loadInbox();
    });
  });
}

function setMsg(msg) {
  const el = document.getElementById("watchMsg");
  if (el) el.textContent = msg;
}

function setCompanyMsg(msg) {
  const el = document.getElementById("companyMsg");
  if (el) el.textContent = msg;
}

async function loadCompanies() {
  const q = document.getElementById("companySearch")?.value.trim() || "";
  const status = document.getElementById("companyStatusFilter")?.value || "active";
  const source = document.getElementById("companySourceFilter")?.value || "";
  const qs = new URLSearchParams({ status, limit: "200" });
  if (q) qs.set("q", q);
  if (source) qs.set("source_reason", source);
  const resp = await fetch(`/api/watched-companies?${qs}`);
  if (!resp.ok) {
    setCompanyMsg("Liste nicht ladbar");
    return;
  }
  const data = await resp.json();
  const items = data.items || [];
  const households = data.households || [];
  document.getElementById("companyCount").textContent = String(data.total || items.length);
  document.getElementById("companyBadge").textContent = String(data.total || items.length);
  const el = document.getElementById("companyList");
  if (!items.length) {
    el.innerHTML = `<p class="fraud-help">Keine Firmen.</p>`;
    return;
  }
  const linked = households.filter((h) => (h.size || 0) >= 2);
  const singles = households
    .filter((h) => (h.size || 0) < 2)
    .flatMap((h) => h.items || []);
  const blocks = [];
  linked.forEach((h) => {
    blocks.push(renderHouseholdCard(h.title, h.people, h.items || [], h.size));
  });
  if (singles.length) {
    const label = linked.length ? "Weitere Firmen" : "Firmen";
    blocks.push(renderHouseholdCard(label, [], singles, singles.length));
  }
  el.innerHTML = `<div class="watch-households">${blocks.join("")}</div>`;
  bindCompanyList(el);
}

function firmHref(c) {
  const qs = new URLSearchParams();
  if (c.company_name) qs.set("company", c.company_name);
  if (c.company_uid) qs.set("uid", c.company_uid);
  qs.set("deep", "3");
  return `/?${qs}`;
}

function renderFirmRow(c) {
  const checked = companySelected.has(c.id) ? "checked" : "";
  const place = c.legal_seat || (c.address || "").split(",").pop()?.trim() || "";
  const cacheState = c.cache_state || "missing";
  const cacheLabel = c.cache_label || "offen";
  const special =
    c.source_reason === "case_open"
      ? `<span class="watch-meta-pill">Fall</span>`
      : c.source_reason === "under_investigation"
        ? `<span class="watch-meta-pill">Abklärung</span>`
        : "";
  return `<div class="watch-firm">
    <label class="watch-merge-label"><input type="checkbox" data-company-id="${c.id}" ${checked} /></label>
    <a class="watch-firm-open" href="${esc(firmHref(c))}">
      <span class="watch-firm-dot watch-cache--${esc(cacheState)}" title="${esc(cacheLabel)}"></span>
      <span class="watch-firm-name">${esc(d(c.company_name, "company"))}</span>
      <span class="watch-firm-place">${esc(place)}</span>
    </a>
    ${special}
    <button type="button" class="watch-firm-archive" data-clear-company="${c.id}">Archiv</button>
  </div>`;
}

function renderHouseholdCard(title, people, members, size) {
  const headTitle =
    people && people.length
      ? people.map((p) => esc(d(p, "person"))).join(" · ")
      : esc(d(title, "company"));
  const count = `${size} ${size === 1 ? "Firma" : "Firmen"}`;
  return `<section class="card watch-household">
    <header class="watch-household-head">
      <div>
        <h3>${headTitle}</h3>
      </div>
      <span class="watch-household-count">${esc(count)}</span>
    </header>
    <div class="watch-household-body">
      ${members.map(renderFirmRow).join("")}
    </div>
  </section>`;
}

function bindCompanyList(el) {
  el.querySelectorAll("input[data-company-id]").forEach((cb) => {
    cb.addEventListener("change", () => {
      const id = Number(cb.dataset.companyId);
      if (cb.checked) companySelected.add(id);
      else companySelected.delete(id);
    });
  });
  el.querySelectorAll("[data-clear-company]").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const id = Number(btn.dataset.clearCompany);
      const r = await fetch(`/api/watched-companies/${id}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "cleared" }),
      });
      if (!r.ok) {
        setCompanyMsg("Archivieren fehlgeschlagen");
        return;
      }
      companySelected.delete(id);
      loadCompanies();
    });
  });
}

function stopBulkPoll() {
  if (bulkPollTimer) {
    clearInterval(bulkPollTimer);
    bulkPollTimer = null;
  }
}

function setBulkStatus(msg) {
  const el = document.getElementById("bulkStatus");
  if (el) el.textContent = msg || "";
}

function resetBulkReview() {
  bulkReviewIndex = 0;
  bulkPicks = new Map();
  bulkHydrated = new Set();
  bulkJobCache = null;
  destroyBulkGraph();
}

function destroyBulkGraph() {
  if (bulkGraphNetwork) {
    try {
      bulkGraphNetwork.destroy();
    } catch (_) { /* ignore */ }
    bulkGraphNetwork = null;
  }
}

function analysisHref(seed) {
  const qs = new URLSearchParams();
  if (seed.name) qs.set("company", seed.name);
  if (seed.uid) qs.set("uid", seed.uid);
  return `/?${qs}`;
}

function pickKeyForGraphNode(it, node) {
  if (!node) return "";
  if (node.type === "company") {
    return companyPickKey(it.id, node.uid, node.label);
  }
  const persons = (it.result && it.result.persons) || [];
  const want = String(node.label || "").toLowerCase();
  const hit = persons.find((p) => String(p.name || "").toLowerCase() === want);
  if (hit) return personPickKey(it.id, hit.name, hit.residence);
  return personPickKey(it.id, node.label, node.residence);
}

function bulkNodeColor(it, node, selected) {
  const former = node.person_status === "former";
  if (node.type === "person") {
    return {
      background: selected ? "#0e7490" : (former ? "#1f2937" : "#164e63"),
      border: selected ? "#22d3ee" : (former ? "#6b7280" : "#67e8f9"),
      highlight: { background: "#155e75", border: "#a5f3fc" },
    };
  }
  if (node.is_seed) {
    return {
      background: selected ? "#083344" : "#111827",
      border: "#22d3ee",
      highlight: { background: "#164e63", border: "#67e8f9" },
    };
  }
  return {
    background: selected ? "#083344" : "#1f2937",
    border: selected ? "#22d3ee" : "#64748b",
    highlight: { background: "#164e63", border: "#67e8f9" },
  };
}

function colorBulkGraph(it) {
  if (!bulkGraphNetwork) return;
  const graph = (it.result && it.result.graph) || {};
  const nodes = graph.nodes || [];
  nodes.forEach((n) => {
    const key = pickKeyForGraphNode(it, n);
    const selected = key && bulkPicks.has(key);
    try {
      bulkGraphNetwork.body.data.nodes.update({
        id: n.id,
        color: bulkNodeColor(it, n, selected),
        borderWidth: selected || n.is_seed ? 3 : 1.5,
      });
    } catch (_) { /* ignore */ }
  });
}

function paintBulkGraph(it, seed) {
  destroyBulkGraph();
  const el = document.getElementById("bulkGraph");
  if (!el) return;
  const graph = (it.result && it.result.graph) || {};
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  if (typeof vis === "undefined") {
    el.innerHTML = `<p class="fraud-help">Graph-Bibliothek nicht geladen.</p>`;
    return;
  }
  if (!nodes.length) {
    el.innerHTML = `<p class="fraud-help">Kein Netz in diesem Scan — Scan erneut starten, dann erscheint das Beziehungsnetz.</p>`;
    return;
  }
  el.innerHTML = "";
  const visNodes = new vis.DataSet(
    nodes.map((n) => {
      const isPerson = n.type === "person";
      const former = n.person_status === "former";
      const key = pickKeyForGraphNode(it, n);
      const selected = key && bulkPicks.has(key);
      const roles = (n.roles || []).slice(0, 3).join(" · ");
      return {
        id: n.id,
        label: d(n.label, isPerson ? "person" : "company") + (roles ? `\n${roles}` : ""),
        shape: isPerson ? "dot" : "box",
        size: isPerson ? (former ? 12 : 16) : undefined,
        font: {
          color: former ? "#9ca3af" : "#f8fafc",
          face: "Rajdhani",
          size: n.is_seed ? 15 : 12,
          bold: !!n.is_seed,
          multi: true,
        },
        color: bulkNodeColor(it, n, selected),
        borderWidth: selected || n.is_seed ? 3 : 1.5,
        opacity: former ? 0.65 : 1,
      };
    })
  );
  const visEdges = new vis.DataSet(
    edges.map((e, i) => {
      const former = e.person_status === "former";
      return {
        id: `e${i}`,
        from: e.from,
        to: e.to,
        label: e.label || "",
        font: { color: "#94a3b8", size: 10, face: "Rajdhani", strokeWidth: 0 },
        color: { color: former ? "#6b7280" : "#22d3ee", opacity: former ? 0.45 : 0.75 },
        dashes: former,
        arrows: "to",
        width: former ? 1 : 1.4,
      };
    })
  );
  bulkGraphNetwork = new vis.Network(
    el,
    { nodes: visNodes, edges: visEdges },
    {
      interaction: { hover: true, tooltipDelay: 80, zoomView: true, dragView: true },
      physics: { stabilization: { iterations: 80 }, barnesHut: { gravitationalConstant: -2800, springLength: 90 } },
      nodes: { margin: 8 },
      edges: { smooth: { type: "continuous" } },
    }
  );
  bulkGraphNetwork.on("click", (params) => {
    const nid = params.nodes && params.nodes[0];
    if (!nid) return;
    const node = nodes.find((n) => n.id === nid);
    const key = pickKeyForGraphNode(it, node);
    if (!key) return;
    const on = !bulkPicks.has(key);
    const entry = entryFromKey(key, it, seed);
    if (on && entry) bulkPicks.set(key, entry);
    else if (!on) bulkPicks.delete(key);
    const input = document.querySelector(`input[data-pick-key="${CSS.escape(key)}"]`);
    if (input) {
      input.checked = on;
      input.closest(".watch-bulk-pick")?.classList.toggle("is-on", on);
    }
    updateBulkCountLabel();
    colorBulkGraph(it);
  });
}

function matchedBulkItems(job) {
  return (job.items || []).filter((it) => it.status === "matched");
}

function missedBulkItems(job) {
  return (job.items || []).filter((it) => it.status !== "matched");
}

function companyPickKey(itemId, uid, name) {
  return `c:${itemId}:${String(uid || name || "").toLowerCase()}`;
}

function personPickKey(itemId, name, residence) {
  return `p:${itemId}:${String(name || "").toLowerCase()}:${String(residence || "").toLowerCase()}`;
}

function seedFromItem(it) {
  const company = (it.result && it.result.company) || {};
  return {
    name: company.name || it.resolved_name || it.input_name || "",
    uid: company.uid || it.resolved_uid || "",
    address: company.address || it.address || "",
    seat: company.legal_seat || it.legal_seat || "",
    ehraid: company.ehraid || it.ehraid || "",
  };
}

function companyEntry(seed) {
  return {
    type: "company",
    company_name: seed.name,
    company_uid: seed.uid || null,
    address: seed.address || null,
    legal_seat: seed.seat || null,
    company_ehraid: seed.ehraid ? Number(seed.ehraid) : null,
  };
}

function personEntry(p, seed) {
  return {
    type: "person",
    display_name: p.name,
    residence: p.residence || null,
    source_company_name: seed.name || null,
    source_company_uid: seed.uid || null,
    company_ehraid: seed.ehraid ? Number(seed.ehraid) : null,
    role: (p.roles || []).join(", ") || null,
  };
}

function hydrateItemPicks(it) {
  if (bulkHydrated.has(it.id)) return;
  bulkHydrated.add(it.id);
  const seed = seedFromItem(it);
  bulkPicks.set(companyPickKey(it.id, seed.uid, seed.name), companyEntry(seed));
  const persons = (it.result && it.result.persons) || [];
  persons
    .filter((p) => p && p.name && (p.status || "current") === "current")
    .forEach((p) => {
      bulkPicks.set(personPickKey(it.id, p.name, p.residence), personEntry(p, seed));
    });
}

function pickCounts() {
  let companies = 0;
  let persons = 0;
  bulkPicks.forEach((e) => {
    if (e.type === "company") companies += 1;
    else if (e.type === "person") persons += 1;
  });
  return { companies, persons, total: companies + persons };
}

function csvCell(value) {
  const text = String(value ?? "");
  if (/[;"\n\r]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

function setReviewActionsVisible(show) {
  const actions = document.getElementById("bulkReviewActions");
  const addBtn = document.getElementById("bulkAddBtn");
  actions?.classList.toggle("hidden", !show);
  addBtn?.classList.toggle("hidden", !show);
}

function renderBulkProgress(items) {
  const wrap = document.getElementById("bulkResults");
  const rows = items
    .map((it) => {
      const seed = seedFromItem(it);
      return `<tr>
        <td>${esc(it.input_name)}</td>
        <td>${esc(seed.name || "—")}</td>
        <td>${esc(it.status)}${it.error_message ? ` · ${esc(it.error_message)}` : ""}</td>
      </tr>`;
    })
    .join("");
  wrap.innerHTML = `<div class="watch-table-wrap"><table class="watch-table">
    <thead><tr><th>Eingabe</th><th>Treffer</th><th>Status</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderPick(key, checked, title, meta) {
  return `<label class="watch-bulk-pick${checked ? " is-on" : ""}">
    <input type="checkbox" data-pick-key="${esc(key)}" ${checked ? "checked" : ""} />
    <span class="watch-bulk-pick-body">
      <strong>${title}</strong>
      ${meta ? `<span class="fraud-help">${meta}</span>` : ""}
    </span>
  </label>`;
}

function renderBulkReview(job) {
  const wrap = document.getElementById("bulkResults");
  const matched = matchedBulkItems(job);
  const missed = missedBulkItems(job);
  if (!matched.length) {
    wrap.innerHTML = `<p class="fraud-help">Keine Treffer zum Reviewen.${
      missed.length ? ` ${missed.length} ohne Fund.` : ""
    }</p>`;
    setReviewActionsVisible(false);
    return;
  }
  if (bulkReviewIndex >= matched.length) bulkReviewIndex = matched.length - 1;
  if (bulkReviewIndex < 0) bulkReviewIndex = 0;
  const it = matched[bulkReviewIndex];
  hydrateItemPicks(it);
  const seed = seedFromItem(it);
  const related = ((it.result && it.result.related_companies) || []).filter(
    (c) => c && (c.name || c.uid)
  );
  const persons = (it.result && it.result.persons) || [];
  const current = persons.filter((p) => p && p.name && (p.status || "current") === "current");
  const former = persons.filter((p) => p && p.name && (p.status || "") === "former");

  const seedKey = companyPickKey(it.id, seed.uid, seed.name);
  const counts = pickCounts();

  const relatedHtml = related.length
    ? related
        .map((c) => {
          const key = companyPickKey(it.id, c.uid, c.name);
          const via = (c.via || []).map((n) => d(n, "person")).filter(Boolean);
          const meta = [c.uid, c.address || c.legal_seat, via.length ? `über ${via.join(", ")}` : ""]
            .filter(Boolean)
            .join(" · ");
          return renderPick(key, bulkPicks.has(key), esc(d(c.name, "company")), esc(meta));
        })
        .join("")
    : `<p class="fraud-help">Keine verwandten Firmen in Suchweite.</p>`;

  const personHtml = (list) =>
    list
      .map((p) => {
        const key = personPickKey(it.id, p.name, p.residence);
        const meta = [p.residence, (p.roles || []).join(", ")].filter(Boolean).join(" · ");
        return renderPick(key, bulkPicks.has(key), esc(d(p.name, "person")), esc(meta));
      })
      .join("") || `<p class="fraud-help">Keine Personen gefunden.</p>`;

  const missHtml = missed.length
    ? `<p class="watch-bulk-miss">Ohne Treffer: ${missed
        .map((m) => esc(m.input_name))
        .join(", ")}</p>`
    : "";

  wrap.innerHTML = `<div class="watch-bulk-review" data-item-id="${esc(it.id)}">
    <div class="watch-bulk-review-head">
      <div>
        <p class="watch-bulk-review-kicker">Firma ${bulkReviewIndex + 1} von ${matched.length}</p>
        <h3>${esc(d(seed.name, "company"))}</h3>
        <p class="fraud-help">${esc([seed.uid, seed.address || seed.seat].filter(Boolean).join(" · ") || "—")}</p>
      </div>
      <p class="fraud-help" id="bulkPickCount">${counts.total} gewählt · ${counts.companies} Firmen · ${counts.persons} Personen</p>
    </div>
    <div class="watch-bulk-graph-wrap">
      <div class="watch-bulk-graph-head">
        <h4>Beziehungsnetz</h4>
        <a class="btn-nav" href="${esc(analysisHref(seed))}" target="_blank" rel="noopener">In Analyse öffnen</a>
      </div>
      <p class="fraud-help">Klick auf Firma oder Person wählt sie aus. Gestrichelte Linie = ehemaliges Mandat.${
        it.result && it.result.cached ? " · aus Cache" : ""
      }</p>
      <div id="bulkGraph" class="watch-bulk-graph"></div>
    </div>
    <div class="watch-bulk-section">
      <h4>Suspect-Firma</h4>
      ${renderPick(
        seedKey,
        bulkPicks.has(seedKey),
        esc(d(seed.name, "company")),
        esc([seed.uid, seed.address].filter(Boolean).join(" · "))
      )}
    </div>
    <div class="watch-bulk-section" data-section="related">
      <h4>Verwandte Firmen
        ${
          related.length
            ? `<span class="watch-bulk-section-tools">
                <button type="button" data-toggle-section="related" data-on="1">alle</button>
                · <button type="button" data-toggle-section="related" data-on="0">keine</button>
              </span>`
            : ""
        }
      </h4>
      ${relatedHtml}
    </div>
    <div class="watch-bulk-section" data-section="current">
      <h4>Aktuelle Organe
        ${
          current.length
            ? `<span class="watch-bulk-section-tools">
                <button type="button" data-toggle-section="current" data-on="1">alle</button>
                · <button type="button" data-toggle-section="current" data-on="0">keine</button>
              </span>`
            : ""
        }
      </h4>
      ${personHtml(current)}
    </div>
    ${
      former.length
        ? `<div class="watch-bulk-section" data-section="former">
            <h4>Ehemalige Organe
              <span class="watch-bulk-section-tools">
                <button type="button" data-toggle-section="former" data-on="1">alle</button>
                · <button type="button" data-toggle-section="former" data-on="0">keine</button>
              </span>
            </h4>
            ${personHtml(former)}
          </div>`
        : ""
    }
    ${missHtml}
  </div>`;

  const prev = document.getElementById("bulkPrevBtn");
  const next = document.getElementById("bulkNextBtn");
  if (prev) prev.disabled = bulkReviewIndex <= 0;
  if (next) {
    next.disabled = false;
    next.textContent =
      bulkReviewIndex >= matched.length - 1 ? "Fertig — zur CSV" : "Nächste Firma";
  }
  setReviewActionsVisible(true);
  bindBulkReview(job, it, seed);
  paintBulkGraph(it, seed);
}

function bindBulkReview(job, it, seed) {
  const wrap = document.getElementById("bulkResults");
  wrap?.querySelectorAll("input[data-pick-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.pickKey;
      const label = input.closest(".watch-bulk-pick");
      if (input.checked) {
        const entry = entryFromKey(key, it, seed);
        if (entry) bulkPicks.set(key, entry);
        label?.classList.add("is-on");
      } else {
        bulkPicks.delete(key);
        label?.classList.remove("is-on");
      }
      updateBulkCountLabel();
      colorBulkGraph(it);
    });
  });
  wrap?.querySelectorAll("[data-toggle-section]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const section = btn.dataset.toggleSection;
      const on = btn.dataset.on === "1";
      const box = wrap.querySelector(`[data-section="${section}"]`);
      box?.querySelectorAll("input[data-pick-key]").forEach((input) => {
        if (input.checked === on) return;
        input.checked = on;
        input.dispatchEvent(new Event("change"));
      });
    });
  });
}

function entryFromKey(key, it, seed) {
  if (key.startsWith("c:")) {
    if (key === companyPickKey(it.id, seed.uid, seed.name)) return companyEntry(seed);
    const related = (it.result && it.result.related_companies) || [];
    const hit = related.find((c) => companyPickKey(it.id, c.uid, c.name) === key);
    if (!hit) return null;
    return companyEntry({
      name: hit.name || "",
      uid: hit.uid || "",
      address: hit.address || "",
      seat: hit.legal_seat || "",
      ehraid: hit.ehraid || "",
    });
  }
  const persons = (it.result && it.result.persons) || [];
  const hit = persons.find((p) => personPickKey(it.id, p.name, p.residence) === key);
  return hit ? personEntry(hit, seed) : null;
}

function updateBulkCountLabel() {
  const el = document.getElementById("bulkPickCount");
  if (!el) return;
  const counts = pickCounts();
  el.textContent = `${counts.total} gewählt · ${counts.companies} Firmen · ${counts.persons} Personen`;
}

function renderBulkResults(job) {
  const wrap = document.getElementById("bulkResults");
  const items = job.items || [];
  if (!wrap) return;
  if (!items.length) {
    wrap.innerHTML = "";
    setReviewActionsVisible(false);
    return;
  }
  if (job.status === "pending" || job.status === "running") {
    renderBulkProgress(items);
    setReviewActionsVisible(false);
    return;
  }
  renderBulkReview(job);
}

function stepBulkReview(delta) {
  const job = bulkJobCache;
  if (!job) return;
  const matched = matchedBulkItems(job);
  if (!matched.length) return;
  const nextIdx = bulkReviewIndex + delta;
  if (nextIdx >= matched.length) {
    exportBulkCsv();
    return;
  }
  bulkReviewIndex = Math.max(0, nextIdx);
  renderBulkReview(job);
}

function exportBulkCsv() {
  const counts = pickCounts();
  if (!counts.total) {
    setBulkStatus("Bitte zuerst Zusammenhänge auswählen");
    return;
  }
  const lines = ["Typ;Name;Adresse;UID;Rolle;Herkunftsfirma"];
  bulkPicks.forEach((e) => {
    if (e.type === "company") {
      lines.push(
        [
          csvCell("Firma"),
          csvCell(e.company_name),
          csvCell(e.address || e.legal_seat || ""),
          csvCell(e.company_uid || ""),
          csvCell(""),
          csvCell(""),
        ].join(";")
      );
    } else {
      lines.push(
        [
          csvCell("Person"),
          csvCell(e.display_name),
          csvCell(e.residence || ""),
          csvCell(""),
          csvCell(e.role || ""),
          csvCell(e.source_company_name || ""),
        ].join(";")
      );
    }
  });
  const blob = new Blob(["\uFEFF" + lines.join("\n") + "\n"], {
    type: "text/csv;charset=utf-8",
  });
  const a = document.createElement("a");
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  a.href = URL.createObjectURL(blob);
  a.download = `lynx_bulk_review_${day}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  setBulkStatus(
    `CSV für Data Science: ${counts.persons} Personen, ${counts.companies} Firmen (Excel: Semikolon, UTF-8)`
  );
}

async function pollBulkJob() {
  if (!bulkJobId) return;
  const resp = await fetch(`/api/bulk-scan/${bulkJobId}`);
  if (!resp.ok) {
    setBulkStatus("Status nicht ladbar");
    stopBulkPoll();
    return;
  }
  const data = await resp.json();
  const job = data.job || {};
  bulkJobCache = job;
  const total = job.total_items || 0;
  const done = job.completed_items || 0;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const wrap = document.getElementById("bulkProgressWrap");
  const bar = document.getElementById("bulkProgressBar");
  wrap?.classList.remove("hidden");
  wrap?.setAttribute("aria-hidden", "false");
  if (bar) bar.style.width = `${pct}%`;
  setBulkStatus(
    `Job #${job.id}: ${job.status} · ${done}/${total}` +
      (job.error_count ? ` · ${job.error_count} ohne Treffer/Fehler` : "")
  );
  renderBulkResults(job);
  if (job.status === "done" || job.status === "failed" || job.status === "cancelled") {
    stopBulkPoll();
  }
}

async function startBulkScan() {
  if (currentUserRole !== "admin") {
    setBulkStatus("Nur für Admins");
    return;
  }
  const text = document.getElementById("bulkNames")?.value || "";
  const level = Number(document.getElementById("bulkLevel")?.value || 3);
  const btn = document.getElementById("bulkStartBtn");
  if (btn) btn.disabled = true;
  stopBulkPoll();
  resetBulkReview();
  try {
    const resp = await fetch("/api/bulk-scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, level, max_person_searches: 4 }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      setBulkStatus(formatDetail(data.detail) || "Start fehlgeschlagen");
      return;
    }
    bulkJobId = data.job?.id;
    setBulkStatus(`Job #${bulkJobId} gestartet…`);
    await pollBulkJob();
    bulkPollTimer = setInterval(pollBulkJob, 2000);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function addBulkSelection() {
  const entries = [...bulkPicks.values()];
  if (!entries.length) {
    setBulkStatus("Bitte zuerst Zusammenhänge auswählen");
    return;
  }
  const resp = await fetch("/api/watchlist/bulk-add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries, source_reason: "bulk_scan" }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    setBulkStatus(formatDetail(data.detail) || "Übernehmen fehlgeschlagen");
    return;
  }
  setBulkStatus(
    `Übernommen: ${data.companies_added || 0} Firmen, ${data.persons_added || 0} Personen`
  );
  loadCompanies();
  loadPersons();
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  return detail ? String(detail) : "";
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

document.querySelectorAll(".watch-tabs .ca-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});
document.getElementById("refreshInboxBtn")?.addEventListener("click", loadInbox);
document.getElementById("alertSeverity")?.addEventListener("change", (e) => {
  alertSeverityFilter = e.target.value;
  renderInbox();
});
document.getElementById("inboxSearch")?.addEventListener("input", (e) => {
  inboxSearchQuery = e.target.value || "";
  renderInbox();
});
document.getElementById("refreshCasesBtn")?.addEventListener("click", loadCases);
document.querySelectorAll("[data-close-modal]").forEach((el) => {
  el.addEventListener("click", closeCaseModal);
});
document.addEventListener("keydown", (e) => {
  const modalOpen = !document.getElementById("caseModal")?.classList.contains("hidden");
  if (e.key === "Escape") {
    if (modalOpen) {
      closeCaseModal();
      return;
    }
    if (document.getElementById("tabInbox")?.classList.contains("is-active") && selectedInboxId) {
      selectedInboxId = null;
      renderInbox();
    }
    return;
  }
  if (modalOpen) return;
  if (!document.getElementById("tabInbox")?.classList.contains("is-active")) return;
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  if (e.key === "ArrowDown" || e.key === "j") {
    e.preventDefault();
    moveInboxSelection(1);
  } else if (e.key === "ArrowUp" || e.key === "k") {
    e.preventDefault();
    moveInboxSelection(-1);
  }
});

let searchTimer = null;
document.getElementById("personSearch")?.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    personOffset = 0;
    loadPersons();
  }, 250);
});
["statusFilter", "sortFilter", "openAlertOnly"].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", () => {
    personOffset = 0;
    loadPersons();
  });
});
document.getElementById("prevPageBtn")?.addEventListener("click", () => {
  personOffset = Math.max(0, personOffset - PAGE_SIZE);
  loadPersons();
});
document.getElementById("nextPageBtn")?.addEventListener("click", () => {
  personOffset += PAGE_SIZE;
  loadPersons();
});

document.getElementById("runMonitoringBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("runMonitoringBtn");
  btn.disabled = true;
  try {
    const r = await fetch("/api/watched-persons/run-monitoring", { method: "POST" });
    const d = await r.json();
    const emailBit =
      d.email && d.email.sent
        ? " · Digest-E-Mail gesendet"
        : d.email && d.email.reason === "no_alerts"
          ? " · keine neuen Funde (keine E-Mail)"
          : d.email && d.email.reason === "smtp_unset"
            ? " · E-Mail: SMTP nicht konfiguriert"
            : d.alerts
              ? " · E-Mail: siehe Server-Log / Empfänger"
              : "";
    const cov = d.coverage && d.coverage.hint ? ` · ${d.coverage.hint}` : "";
    setMsg(
      `Liste fortgesetzt: ${d.scanned || 0} Personen, ${d.new_links || 0} neue Firmen, ${d.alerts || 0} Alerts${emailBit}${cov}`
    );
    await Promise.all([loadPersons(), loadInbox()]);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("runHighPriorityBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("runHighPriorityBtn");
  if (!btn) return;
  btn.disabled = true;
  try {
    const r = await fetch("/api/watched-persons/run-high-priority-monitoring", {
      method: "POST",
    });
    const d = await r.json();
    if (!r.ok) {
      setMsg(d.detail || "High-Prio-Scan fehlgeschlagen");
      return;
    }
    const sel = d.selection || {};
    const emailBit =
      d.email && d.email.sent
        ? " · Digest-E-Mail gesendet"
        : d.email && d.email.reason === "no_alerts"
          ? " · keine neuen Funde"
          : "";
    setMsg(
      `Priorisierte geprüft: ${d.scanned || 0} (high ${sel.high_priority_selected ?? "—"}) · ` +
        `${d.new_links || 0} neue Firmen · ${d.alerts || 0} Alerts${emailBit}`
    );
    await Promise.all([loadPersons(), loadInbox()]);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("addPersonBtn")?.addEventListener("click", async () => {
  const name = document.getElementById("manualName").value.trim();
  if (!name) return;
  const residence = document.getElementById("manualResidence").value.trim() || null;
  const r = await fetch("/api/watched-persons", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: name, residence }),
  });
  const d = await r.json();
  setMsg(d.already_existed ? `Bereits vorhanden (#${d.id})` : `Hinzugefügt (#${d.id})`);
  document.getElementById("manualName").value = "";
  personOffset = 0;
  selectedPersonId = d.id;
  await loadPersons();
  openCaseModal(d.id);
});

document.getElementById("mergeBtn")?.addEventListener("click", async () => {
  const ids = [...mergeSelected];
  if (ids.length !== 2) {
    setMsg("Genau zwei Personen für Merge auswählen");
    return;
  }
  const reason = prompt("Begründung für Merge", "Dieselbe Person") || "";
  if (reason.length < 3) return;
  const r = await fetch("/api/watched-persons/merge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ canonical_id: ids[0], duplicate_id: ids[1], reason }),
  });
  const d = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Merge fehlgeschlagen");
    return;
  }
  setMsg(`Merge OK: #${d.duplicate_id} → #${d.canonical_id}`);
  mergeSelected.clear();
  selectedPersonId = d.canonical_id;
  loadPersons();
  openCaseModal(d.canonical_id, { autoScan: false });
});

document.getElementById("deletePersonsBtn")?.addEventListener("click", async () => {
  const ids = [...mergeSelected];
  if (!ids.length) {
    setMsg("Mindestens eine Person zum Löschen auswählen (Checkbox)");
    return;
  }
  if (!confirm(`${ids.length} Person(en) unwiderruflich von der Watchlist löschen?`)) return;
  const r = await fetch("/api/watched-persons/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  const d = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Löschen fehlgeschlagen");
    return;
  }
  setMsg(`${d.deleted_count || ids.length} Person(en) gelöscht`);
  mergeSelected.clear();
  if (ids.includes(selectedPersonId)) {
    selectedPersonId = null;
    closeCaseModal();
  }
  loadPersons();
  loadInbox();
  loadCases();
});

document.getElementById("companyStatusFilter")?.addEventListener("change", loadCompanies);
document.getElementById("companySourceFilter")?.addEventListener("change", loadCompanies);
let companySearchTimer = null;
document.getElementById("companySearch")?.addEventListener("input", () => {
  clearTimeout(companySearchTimer);
  companySearchTimer = setTimeout(loadCompanies, 250);
});
document.getElementById("refreshCompanyCacheBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("refreshCompanyCacheBtn");
  if (btn) btn.disabled = true;
  setCompanyMsg("Profile werden geladen…");
  try {
    const r = await fetch("/api/watched-companies/refresh-cache", { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      setCompanyMsg(formatDetail(d.detail) || "Laden fehlgeschlagen");
      return;
    }
    const n = d.refreshed || 0;
    const err = (d.errors || []).length;
    setCompanyMsg(
      n
        ? `${n} Profil${n === 1 ? "" : "e"} geladen` + (err ? ` · ${err} ohne Treffer` : "")
        : err
          ? "Keine neuen Profile"
          : "Alle Profile sind aktuell"
    );
    await loadCompanies();
  } finally {
    if (btn) btn.disabled = false;
  }
});
document.getElementById("archiveCompaniesBtn")?.addEventListener("click", async () => {
  const ids = [...companySelected];
  if (!ids.length) {
    setCompanyMsg("Bitte eine Firma ankreuzen");
    return;
  }
  for (const id of ids) {
    const r = await fetch(`/api/watched-companies/${id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "cleared" }),
    });
    if (!r.ok) {
      setCompanyMsg("Archivieren fehlgeschlagen");
      return;
    }
  }
  companySelected.clear();
  loadCompanies();
});
document.getElementById("deleteCompaniesBtn")?.addEventListener("click", async () => {
  const ids = [...companySelected];
  if (!ids.length) {
    setCompanyMsg("Bitte eine Firma ankreuzen");
    return;
  }
  if (!confirm(`${ids.length} Firma(en) von der Watchlist löschen?`)) return;
  const r = await fetch("/api/watched-companies/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  const d = await r.json();
  if (!r.ok) {
    setCompanyMsg(formatDetail(d.detail) || "Löschen fehlgeschlagen");
    return;
  }
  companySelected.clear();
  setCompanyMsg(`${d.deleted_count || ids.length} Firma(en) gelöscht`);
  loadCompanies();
});
document.getElementById("bulkStartBtn")?.addEventListener("click", startBulkScan);
document.getElementById("bulkAddBtn")?.addEventListener("click", addBulkSelection);
document.getElementById("bulkPrevBtn")?.addEventListener("click", () => stepBulkReview(-1));
document.getElementById("bulkNextBtn")?.addEventListener("click", () => stepBulkReview(1));
document.getElementById("bulkExportCsvBtn")?.addEventListener("click", exportBulkCsv);

const params = new URLSearchParams(location.search);
const personParam = params.get("person");
const tabParam = params.get("tab");
loadMe().then(async () => {
  await loadInbox();
  await loadCompanies();
  if (tabParam && ["inbox", "companies", "persons", "cases", "bulk"].includes(tabParam)) {
    if (tabParam === "bulk" && currentUserRole !== "admin") switchTab("companies");
    else switchTab(tabParam);
  }
  if (personParam) openPersonFromInbox(personParam);
});
