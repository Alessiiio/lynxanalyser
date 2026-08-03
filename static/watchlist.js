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
let selectedPersonId = null;
let mergeSelected = new Set();
let currentDossier = null;

async function loadMe() {
  const resp = await fetch("/api/me");
  if (!resp.ok) return;
  const data = await resp.json();
  const el = document.getElementById("watchUser");
  if (el && data.user) el.textContent = `${d(data.user.display_name, "user")} (${data.user.role})`;
  if (data.settings && typeof applyAnonymizeMode === "function") {
    applyAnonymizeMode(!!data.settings.anonymize_mode, { silent: true });
  }
}

function switchTab(name) {
  document.querySelectorAll(".watch-tabs .ca-tab").forEach((t) => {
    t.classList.toggle("is-active", t.dataset.tab === name);
  });
  document.getElementById("tabInbox").classList.toggle("is-active", name === "inbox");
  document.getElementById("tabPersons").classList.toggle("is-active", name === "persons");
  document.getElementById("tabCases").classList.toggle("is-active", name === "cases");
  if (name === "persons") loadPersons();
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
    ? `<a class="btn-nav" href="/cases/${p.linked_case_id}">Zur Firmenakte #${p.linked_case_id}</a>`
    : `<span class="watch-no-case-badge">Ohne Akte</span>
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
              <div class="fraud-side-item-title">${esc(a.severity)} · ${esc(a.alert_type)}
                ${a.acknowledged ? `<span class="fraud-speed-hint">quittiert</span>` : ""}
              </div>
              <div class="fraud-entry-meta">${esc(a.message)}</div>
              <div class="fraud-side-links">
                ${!a.acknowledged ? `<button type="button" class="btn-nav" data-ack="${a.id}">Quittieren</button>` : ""}
                <button type="button" class="btn-check" data-to-case="${a.id}">In neue Akte überführen</button>
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
        setCaseStatus(formatDetail(d.detail) || "Überführung fehlgeschlagen");
        btn.disabled = false;
        return;
      }
      setCaseStatus(`Akte #${d.id} eröffnet (${d.already_existed ? "bereits vorhanden" : "neu"})`);
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

async function loadInbox() {
  const resp = await fetch("/api/watchlist/inbox?limit=150");
  const data = await resp.json();
  let items = data.items || [];
  if (alertSeverityFilter) {
    items = items.filter((it) => it.payload && it.payload.severity === alertSeverityFilter);
  }
  document.getElementById("inboxBadge").textContent = String(data.total || items.length);
  const el = document.getElementById("inboxList");
  if (!items.length) {
    el.innerHTML = `<p class="fraud-help">Keine offenen Funde.</p>`;
    return;
  }
  el.innerHTML = `<ul class="fraud-side-list">${items.map((it) => {
    const a = it.payload;
    return `<li class="watch-inbox-item">
      <button type="button" class="watch-person-summary" data-open-person="${a.person_id || ""}">
        <span class="fraud-side-item-title">${esc(a.severity)} · ${esc(a.alert_type)}</span>
        <span class="fraud-entry-meta">${esc(a.message)}</span>
        <span class="fraud-entry-meta">
          ${a.person_name ? `<span>${esc(a.person_name)}</span>` : ""}
          ${a.source_company_name ? `<span>Quelle: ${esc(d(a.source_company_name, "company"))}</span>` : ""}
        </span>
      </button>
      <div class="fraud-side-links">
        ${a.person_id ? `<button type="button" class="btn-nav" data-open-person="${a.person_id}">Akte öffnen</button>` : ""}
        <button type="button" class="btn-check" data-from-alert="${a.id}">In neue Akte überführen</button>
        <button type="button" class="btn-nav" data-ack="${a.id}">Quittieren</button>
      </div>
    </li>`;
  }).join("")}</ul>`;

  el.querySelectorAll("[data-open-person]").forEach((btn) => {
    btn.addEventListener("click", () => openPersonFromInbox(btn.dataset.openPerson));
  });
  el.querySelectorAll("[data-from-alert]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      const r = await fetch(`/api/company-cases/from-alert/${btn.dataset.fromAlert}`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) {
        setMsg(formatDetail(d.detail) || "Überführung fehlgeschlagen");
        btn.disabled = false;
        return;
      }
      if (d.id) location.href = `/cases/${d.id}`;
    });
  });
  el.querySelectorAll("[data-ack]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/api/network-alerts/${btn.dataset.ack}/ack`, { method: "POST" });
      loadInbox();
    });
  });
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
      ? `<span class="watch-case-link-badge" title="Mit Firmenakte">Akte #${esc(String(p.linked_case_id || ""))}</span>`
      : `<span class="watch-no-case-badge" title="Frühwarnung ohne Firmenakte — Akte eröffnen empfohlen">Ohne Akte</span>`;
    return `<li class="watch-person-row${active}${p.probable_intermediary ? " is-intermediary-collapsed" : ""}${p.has_company_case ? "" : " is-no-case"}">
      <button type="button" class="watch-person-summary" data-select="${p.id}">
        <span class="fraud-side-item-title">${esc(d(p.display_name, "person"))}
          <span class="fraud-speed-hint">${esc(p.status)}</span>${inter}${undesired}${aml}${caseBadge}
        </span>
        <span class="fraud-entry-meta">
          <span>${p.company_count || 0} Firmen</span>
          <span>${p.open_alert_count || 0} Alerts</span>
          <span>Prio ${p.priority_score ?? "—"}</span>
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
  loadInbox();
});
document.getElementById("refreshCasesBtn")?.addEventListener("click", loadCases);
document.querySelectorAll("[data-close-modal]").forEach((el) => {
  el.addEventListener("click", closeCaseModal);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeCaseModal();
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
    const r = await fetch("/api/watched-persons/run-monitoring?limit=5", { method: "POST" });
    const d = await r.json();
    setMsg(`Monitoring: ${d.scanned || 0} Personen, ${d.alerts || 0} Alerts`);
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

const params = new URLSearchParams(location.search);
const personParam = params.get("person");
loadMe().then(async () => {
  await loadInbox();
  if (personParam) openPersonFromInbox(personParam);
});
