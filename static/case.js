/** CompanyCase detail wizard. */

const CASE_ID = Number(location.pathname.split("/").pop());

const STATUS_LABELS = {
  under_review: "In Prüfung",
  confirmed_fraud: "Betrug bestätigt",
  ready_for_report: "Report bereit",
  reported: "Gemeldet",
  closed: "Geschlossen (Compliance)",
  cleared: "Kein Betrug",
};

const ENTITY_LABELS = {
  company: "Firma",
  person: "Person",
};

function d(value, kind) {
  return typeof anon === "function" ? anon(value, kind) : value;
}

let currentCase = null;
/** @type {number} */
let docsWizardIndex = 0;

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

function setMsg(t) {
  document.getElementById("caseMsg").textContent = t || "";
}

function updateStepper(status) {
  const order = ["review", "confirm", "docs", "report", "closed"];
  let active = "review";
  if (status === "cleared") active = "confirm";
  else if (status === "under_review") active = "confirm";
  else if (status === "confirmed_fraud") active = "docs";
  else if (status === "ready_for_report") active = "report";
  else if (status === "reported") active = "closed";
  else if (status === "closed") active = "closed";

  const activeIdx = order.indexOf(active);
  document.querySelectorAll("#caseStepper .fraud-step").forEach((el) => {
    const idx = order.indexOf(el.dataset.step);
    el.classList.toggle("is-active", idx === activeIdx);
    el.classList.toggle("is-done", idx < activeIdx || status === "closed" || (status === "cleared" && idx <= 1));
  });
}

function setNextStep(text) {
  const el = document.getElementById("caseNextStep");
  if (!el) return;
  if (!text) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = text;
  el.classList.remove("hidden");
}

function hitContextBits(c) {
  const bits = [];
  if (c.hit_amount != null) bits.push(`${c.hit_amount} ${c.hit_currency || "CHF"}`);
  if (c.hit_reference) bits.push(`Zweck/Ref: ${c.hit_reference}`);
  if (c.hit_note) bits.push(c.hit_note);
  return bits;
}

function hasHitContext(c) {
  return c.hit_amount != null
    || !!(c.hit_reference || "").trim()
    || !!(c.hit_note || "").trim();
}

function setHitContextCollapsed(collapsed) {
  const panel = document.getElementById("panelHitContext");
  const toggle = document.getElementById("hitContextToggle");
  if (!panel || !toggle) return;
  panel.classList.toggle("is-collapsed", !!collapsed);
  toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
}

function renderHitContext(c) {
  const editable = ["under_review", "confirmed_fraud", "ready_for_report"].includes(c.status);
  const form = document.getElementById("hitContextForm");
  const saveBtn = document.getElementById("saveHitContextBtn");
  const readonly = document.getElementById("hitContextReadonly");
  const summary = document.getElementById("hitContextSummary");
  const amount = document.getElementById("hitAmount");
  const currency = document.getElementById("hitCurrency");
  const reference = document.getElementById("hitReference");
  const note = document.getElementById("hitNote");
  if (amount) amount.value = c.hit_amount != null ? c.hit_amount : "";
  if (currency) currency.value = c.hit_currency || "CHF";
  if (reference) reference.value = c.hit_reference || "";
  if (note) note.value = c.hit_note || "";

  const bits = hitContextBits(c);
  if (summary) {
    summary.textContent = bits.length ? bits.join(" · ") : (hasHitContext(c) ? "Erfasst" : "Noch leer — optional ausfüllen");
  }

  form?.classList.toggle("hidden", !editable);
  saveBtn?.parentElement?.classList.toggle("hidden", !editable);
  saveBtn?.classList.toggle("hidden", !editable);
  if (!editable) {
    if (readonly) {
      readonly.textContent = bits.length ? bits.join(" · ") : "Kein Zahlungs-Hit erfasst.";
      readonly.classList.remove("hidden");
    }
    setHitContextCollapsed(true);
  } else {
    readonly?.classList.add("hidden");
    // Done → collapsed; empty → open for entry
    setHitContextCollapsed(hasHitContext(c));
  }
}

function docsWizardSteps(c) {
  const items = c.bank_checks || [];
  const steps = [
    {
      id: "payment",
      kind: "payment",
      short: "Sicherung",
      done: !!c.payment_blocked || !!(c.payment_blocked_note || "").trim(),
    },
    ...items.map((item) => ({
      id: `check-${item.id}`,
      kind: "bank_check",
      item,
      short: item.entity_type === "person" ? "Person" : "Firma",
      done: item.status !== "pending",
    })),
    {
      id: "journal",
      kind: "journal",
      short: "Journal",
      done: (c.journal || []).length > 0,
    },
  ];
  return steps;
}

function wizardIcon(kind, entityType) {
  if (kind === "payment") {
    return `<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="currentColor" d="M24 4 8 10v12c0 11 6.8 18.6 16 22 9.2-3.4 16-11 16-22V10L24 4zm0 4.2 12 4.5v9.3c0 8.4-5 14.5-12 17.4-7-2.9-12-9-12-17.4v-9.3l12-4.5zm-1.5 9.3v8.5h3V17.5h-3zm0 11v3h3v-3h-3z"/></svg>`;
  }
  if (kind === "journal") {
    return `<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="currentColor" d="M12 6h20a4 4 0 0 1 4 4v28a4 4 0 0 1-4 4H12a4 4 0 0 1-4-4V10a4 4 0 0 1 4-4zm0 2a2 2 0 0 0-2 2v28a2 2 0 0 0 2 2h20a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2H12zm4 8h12v2H16v-2zm0 6h12v2H16v-2zm0 6h8v2h-8v-2z"/></svg>`;
  }
  if (entityType === "person") {
    return `<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="currentColor" d="M24 8a8 8 0 1 1 0 16 8 8 0 0 1 0-16zm0 2a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM10 38c0-7.2 6.3-12 14-12s14 4.8 14 12v2H10v-2zm2 .2c.6-5 5.6-8.2 12-8.2s11.4 3.2 12 8.2H12z"/></svg>`;
  }
  return `<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="currentColor" d="M8 40V16l16-10 16 10v24H8zm2-2h28V17.2L24 8.4 10 17.2V38zm6-4h16v2H16v-2zm0-6h16v2H16v-2zm0-6h10v2H16v-2z"/></svg>`;
}

function renderDocsWizard(c) {
  const steps = docsWizardSteps(c);
  if (!steps.length) return;

  if (docsWizardIndex >= steps.length) docsWizardIndex = steps.length - 1;
  if (docsWizardIndex < 0) docsWizardIndex = 0;

  const nav = document.getElementById("docsWizardNav");
  const stage = document.getElementById("docsWizardStage");
  if (!nav || !stage) return;

  nav.innerHTML = steps.map((s, i) => {
    const classes = [
      "docs-wiz-pill",
      i === docsWizardIndex ? "is-current" : "",
      s.done ? "is-done" : "",
    ].filter(Boolean).join(" ");
    return `<button type="button" class="${classes}" data-wiz-goto="${i}">
      <span class="docs-wiz-pill-num">${s.done ? "✓" : i + 1}</span>
      <span>${esc(s.short)}</span>
    </button>`;
  }).join("");

  nav.querySelectorAll("[data-wiz-goto]").forEach((btn) => {
    btn.addEventListener("click", () => {
      docsWizardIndex = Number(btn.dataset.wizGoto);
      renderDocsWizard(currentCase);
    });
  });

  const step = steps[docsWizardIndex];
  const isLast = docsWizardIndex === steps.length - 1;
  const progressLabel = `Schritt ${docsWizardIndex + 1} von ${steps.length}`;

  let body = "";
  if (step.kind === "payment") {
    body = `
      <div class="docs-wiz-icon" aria-hidden="true">${wizardIcon("payment")}</div>
      <p class="docs-wiz-kicker">${esc(progressLabel)} · Sicherungsmassnahme</p>
      <h3 class="docs-wiz-title">Wurde die Zahlung blockiert?</h3>
      <p class="docs-wiz-lead">Halte fest, ob die Kernbank die Zahlung gestoppt hat — ohne Kundendaten, nur interne Referenz.</p>
      <div class="docs-wiz-fields">
        <label class="docs-wiz-check">
          <input type="checkbox" id="wizPaymentBlocked" ${c.payment_blocked ? "checked" : ""}>
          <span>Ja — Zahlung ist blockiert</span>
        </label>
        <label class="docs-wiz-field-label" for="wizPaymentNote">Kernbanken-Referenz</label>
        <input type="text" id="wizPaymentNote" class="watch-reason docs-wiz-input"
          placeholder="z. B. Ticket-/Fallnummer (keine Kundendaten)"
          maxlength="512" value="${esc(c.payment_blocked_note || "")}">
      </div>
      <div class="docs-wiz-actions">
        <button type="button" class="btn-nav docs-wiz-btn" data-wiz-prev ${docsWizardIndex === 0 ? "disabled" : ""}>Zurück</button>
        <button type="button" class="btn-case-equal btn-case-confirm docs-wiz-btn" data-wiz-save-payment>
          Speichern &amp; weiter
        </button>
      </div>`;
  } else if (step.kind === "bank_check") {
    const item = step.item;
    const typeLabel = ENTITY_LABELS[item.entity_type] || item.entity_type;
    const pending = item.status === "pending";
    const subject = d(item.entity_label, item.entity_type === "person" ? "person" : "company");
    body = `
      <div class="docs-wiz-icon" aria-hidden="true">${wizardIcon("bank_check", item.entity_type)}</div>
      <h3 class="docs-wiz-title">Kundenbeziehung?</h3>
      <p class="docs-wiz-subject"><strong>${esc(subject)}</strong> · ${esc(typeLabel)}</p>
      <div class="docs-wiz-lookup">
        <p>PDF mit Namen zum Abgleich in den Kernbanksystemen.</p>
        <button type="button" class="btn-nav docs-wiz-btn" data-wiz-lookup-pdf>PDF Abgleichsliste</button>
      </div>
      ${pending ? `
        <div class="docs-wiz-answer-row" role="group" aria-label="Ergebnis">
          <button type="button" class="docs-wiz-answer is-yes" data-wiz-answer="relationship_found">
            <span class="docs-wiz-answer-ico" aria-hidden="true">✓</span>
            <span class="docs-wiz-answer-label">Kundenbeziehung vorhanden</span>
          </button>
          <button type="button" class="docs-wiz-answer is-no" data-wiz-answer="no_relationship">
            <span class="docs-wiz-answer-ico" aria-hidden="true">✕</span>
            <span class="docs-wiz-answer-label">Kein Kunde</span>
          </button>
        </div>
        <input type="hidden" id="wizCheckStatus" value="" data-status>
        <div class="docs-wiz-note-block">
          <button type="button" class="docs-wiz-note-toggle" data-wiz-toggle-note id="wizCheckNoteToggle">
            + Verweis / Nachweis (optional)
          </button>
          <div class="docs-wiz-fields docs-wiz-note-wrap hidden" id="wizCheckNoteWrap">
            <label class="docs-wiz-field-label" for="wizCheckNote">Verweis / Nachweis</label>
            <input id="wizCheckNote" class="watch-reason docs-wiz-input" data-note
              placeholder="Interne Referenz, Systemhinweis…" maxlength="512" />
          </div>
        </div>
        <p class="docs-wiz-inline-msg fraud-help" id="wizCheckMsg" hidden></p>
        <div class="docs-wiz-actions">
          <button type="button" class="btn-nav docs-wiz-btn" data-wiz-prev>Zurück</button>
          <button type="button" class="btn-case-equal btn-case-confirm docs-wiz-btn" data-wiz-save-check="${item.id}" disabled>
            Speichern &amp; weiter
          </button>
        </div>
      ` : `
        <div class="docs-wiz-done-box">
          <span class="docs-wiz-done-badge">Erledigt</span>
          <p>${esc(item.status === "relationship_found" ? "Kundenbeziehung vorhanden" : "Kein Kunde")}</p>
          <p class="fraud-help">${esc(item.checked_by || "")} · ${esc(item.checked_at || "")}${item.note ? ` — ${esc(item.note)}` : ""}</p>
        </div>
        <div class="docs-wiz-actions">
          <button type="button" class="btn-nav docs-wiz-btn" data-wiz-prev>Zurück</button>
          <button type="button" class="btn-case-equal btn-case-confirm docs-wiz-btn" data-wiz-next>
            ${isLast ? "Fertig" : "Weiter"}
          </button>
        </div>
      `}`;
  } else {
    const journal = c.journal || [];
    body = `
      <div class="docs-wiz-icon" aria-hidden="true">${wizardIcon("journal")}</div>
      <p class="docs-wiz-kicker">${esc(progressLabel)} · Abklärung</p>
      <h3 class="docs-wiz-title">Was wurde abgeklärt?</h3>
      <p class="docs-wiz-lead">Kurz das Ergebnis des Kundengesprächs oder der internen Prüfung festhalten — für Compliance und Report.</p>
      ${journal.length ? `
        <ul class="docs-wiz-journal">${journal.map((e) => `
          <li>
            <div class="docs-wiz-journal-meta">${esc(e.author)} · ${esc((e.created_at || "").slice(0, 16))}</div>
            <div>${esc(e.text)}</div>
          </li>`).join("")}</ul>
      ` : `<p class="fraud-help">Noch keine Journal-Einträge.</p>`}
      <div class="docs-wiz-fields">
        <label class="docs-wiz-field-label" for="wizJournalText">Neuer Eintrag</label>
        <textarea id="wizJournalText" class="fraud-net-textarea docs-wiz-input" rows="4"
          placeholder="Abklärungsergebnis…" maxlength="4000"></textarea>
      </div>
      <div class="docs-wiz-add-check">
        <p class="docs-wiz-field-label">Checkliste erweitern (optional)</p>
        <p class="fraud-help">Standard: nur Firma + aktuelle Organe. Ehemalige / weitere Firmen hier nachziehen.</p>
        <div class="docs-wiz-add-row">
          <select id="wizAddType" class="ca-select">
            <option value="person">Person</option>
            <option value="company">Firma</option>
          </select>
          <input id="wizAddLabel" class="watch-reason docs-wiz-input" placeholder="Name / Firma" maxlength="512">
          <button type="button" class="btn-nav docs-wiz-btn" data-wiz-add-check>Hinzufügen</button>
        </div>
      </div>
      <div class="docs-wiz-actions">
        <button type="button" class="btn-nav docs-wiz-btn" data-wiz-prev>Zurück</button>
        <button type="button" class="btn-case-equal btn-case-confirm docs-wiz-btn" data-wiz-save-journal>
          Eintrag speichern
        </button>
        ${isLast && (c.documentation_complete || journal.length) ? `
          <button type="button" class="btn-nav docs-wiz-btn" data-wiz-to-report>Zum Reporting →</button>
        ` : ""}
      </div>`;
  }

  stage.innerHTML = `<article class="docs-wiz-card">${body}</article>`;

  stage.querySelector("[data-wiz-prev]")?.addEventListener("click", () => {
    docsWizardIndex = Math.max(0, docsWizardIndex - 1);
    renderDocsWizard(currentCase);
  });
  stage.querySelector("[data-wiz-next]")?.addEventListener("click", () => {
    docsWizardIndex = Math.min(steps.length - 1, docsWizardIndex + 1);
    renderDocsWizard(currentCase);
  });
  stage.querySelector("[data-wiz-to-report]")?.addEventListener("click", () => {
    document.getElementById("panelReport")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  stage.querySelector("[data-wiz-save-payment]")?.addEventListener("click", () => savePaymentFromWizard(true));
  stage.querySelector("[data-wiz-lookup-pdf]")?.addEventListener("click", () => downloadLookupSheet());
  stage.querySelector("[data-wiz-toggle-note]")?.addEventListener("click", () => {
    const wrap = document.getElementById("wizCheckNoteWrap");
    const toggle = document.getElementById("wizCheckNoteToggle");
    if (!wrap) return;
    const nowHidden = wrap.classList.toggle("hidden");
    if (toggle) {
      toggle.textContent = nowHidden
        ? "+ Verweis / Nachweis (optional)"
        : "Verweis ausblenden";
    }
    if (!nowHidden) document.getElementById("wizCheckNote")?.focus();
  });
  stage.querySelectorAll("[data-wiz-answer]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const status = btn.getAttribute("data-wiz-answer");
      const hidden = document.getElementById("wizCheckStatus");
      if (hidden) hidden.value = status || "";
      stage.querySelectorAll("[data-wiz-answer]").forEach((b) => {
        b.classList.toggle("is-selected", b === btn);
      });
      const saveBtn = stage.querySelector("[data-wiz-save-check]");
      if (saveBtn) saveBtn.disabled = false;
      const msg = document.getElementById("wizCheckMsg");
      if (msg) {
        msg.hidden = true;
        msg.textContent = "";
      }
    });
  });
  stage.querySelector("[data-wiz-save-check]")?.addEventListener("click", async (ev) => {
    const id = ev.currentTarget.getAttribute("data-wiz-save-check");
    await saveBankCheckFromWizard(id, true);
  });
  stage.querySelector("[data-wiz-save-journal]")?.addEventListener("click", () => saveJournalFromWizard(false));
  stage.querySelector("[data-wiz-add-check]")?.addEventListener("click", addBankCheckFromWizard);
}

async function downloadLookupSheet() {
  try {
    const resp = await fetch(`/api/company-cases/${CASE_ID}/lookup-sheet`, {
      credentials: "same-origin",
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const err = await resp.json();
        detail = formatDetail(err.detail) || detail;
      } catch (_) { /* ignore */ }
      setMsg(`PDF fehlgeschlagen: ${detail}`);
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `abgleich_akte_${CASE_ID}.pdf`;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (err) {
    setMsg(err.message || "PDF fehlgeschlagen");
  }
}

async function savePaymentFromWizard(advance) {
  const blocked = !!document.getElementById("wizPaymentBlocked")?.checked;
  const note = document.getElementById("wizPaymentNote")?.value || null;
  const r = await fetch(`/api/company-cases/${CASE_ID}/payment`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payment_blocked: blocked, payment_blocked_note: note }),
  });
  const d = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Fehler");
    return;
  }
  if (advance) docsWizardIndex += 1;
  renderCase(d);
  setMsg("Sicherungsmassnahmen gespeichert");
}

async function saveBankCheckFromWizard(itemId, advance) {
  const status =
    document.getElementById("wizCheckStatus")?.value
    || document.querySelector("[data-wiz-answer].is-selected")?.getAttribute("data-wiz-answer")
    || "";
  const note = document.getElementById("wizCheckNote")?.value?.trim() || "";
  const inline = document.getElementById("wizCheckMsg");
  const showErr = (t) => {
    setMsg(t);
    if (inline) {
      inline.hidden = false;
      inline.textContent = t;
    }
  };
  if (!status) {
    showErr("Bitte Ergebnis wählen (Haken oder Kreuz)");
    return;
  }
  if (!itemId) {
    showErr("Eintrag nicht gefunden");
    return;
  }
  const saveBtn = document.querySelector("[data-wiz-save-check]");
  if (saveBtn) saveBtn.disabled = true;
  try {
    const r = await fetch(`/api/company-cases/${CASE_ID}/bank-checks/${itemId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ status, note }),
    });
    let d = {};
    try {
      d = await r.json();
    } catch (_) {
      d = {};
    }
    if (!r.ok) {
      showErr(formatDetail(d.detail) || `Speichern fehlgeschlagen (${r.status})`);
      if (saveBtn) saveBtn.disabled = false;
      return;
    }
    if (advance) docsWizardIndex += 1;
    renderCase(d);
    setMsg("Checklisten-Eintrag gespeichert");
  } catch (err) {
    showErr(err.message || "Speichern fehlgeschlagen");
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function saveJournalFromWizard() {
  const text = document.getElementById("wizJournalText")?.value?.trim() || "";
  if (!text) {
    setMsg("Bitte Text eingeben");
    return;
  }
  const r = await fetch(`/api/company-cases/${CASE_ID}/journal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const d = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Fehler");
    return;
  }
  renderCase(d);
  setMsg("Journal-Eintrag hinzugefügt");
}

async function addBankCheckFromWizard() {
  const entity_type = document.getElementById("wizAddType")?.value || "person";
  const entity_label = document.getElementById("wizAddLabel")?.value?.trim() || "";
  if (entity_label.length < 2) {
    setMsg("Name / Firma angeben");
    return;
  }
  const r = await fetch(`/api/company-cases/${CASE_ID}/bank-checks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entity_type, entity_label }),
  });
  const d = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Hinzufügen fehlgeschlagen");
    return;
  }
  // Jump to the new pending check (last steps before journal)
  const steps = docsWizardSteps(d);
  const idx = steps.findIndex((s) => s.kind === "bank_check" && s.item && !s.done);
  docsWizardIndex = idx >= 0 ? idx : Math.max(0, steps.length - 2);
  renderCase(d);
  setMsg("Checklisten-Eintrag hinzugefügt");
}

function renderCase(c) {
  currentCase = c;
  document.getElementById("caseTitle").textContent = d(c.company_name, "company") || "Akte";
  document.getElementById("caseSub").textContent =
    `${STATUS_LABELS[c.status] || c.status}` +
    (c.company_uid ? ` · ${d(c.company_uid, "uid")}` : "") +
    ` · eröffnet von ${d(c.opened_by, "user")}`;

  if (c.status === "under_review" && c.opened_at) {
    const days = Math.floor((Date.now() - Date.parse(c.opened_at)) / 86400000);
    if (days >= 3 && !Number.isNaN(days)) {
      setMsg(`Hinweis: Akte seit ${days} Tagen in Prüfung — bitte bestätigen oder schliessen.`);
    } else {
      setMsg("");
    }
  }

  updateStepper(c.status);

  if (c.status === "under_review") {
    setNextStep("Nächster Schritt: Kundengespräch führen → Betrugsart wählen → bestätigen oder schliessen.");
  } else if (c.status === "confirmed_fraud") {
    setNextStep("Nächster Schritt: Dokumentation — Zahlung sichern, Bankbeziehungen prüfen, Journal.");
  } else if (c.status === "ready_for_report") {
    setNextStep("Nächster Schritt: Report erzeugen und an Compliance übergeben.");
  } else if (c.status === "reported") {
    setNextStep("Nächster Schritt: Compliance schliesst den Fall mit Notiz (Actioned).");
  } else if (c.status === "closed" || c.status === "cleared") {
    setNextStep("Fall abgeschlossen — Akte bleibt für das Team einsehbar (Audit).");
  } else {
    setNextStep("");
  }

  renderHitContext(c);

  const confirmPanel = document.getElementById("panelConfirm");
  const docsPanel = document.getElementById("panelDocs");
  const reportPanel = document.getElementById("panelReport");
  const compliancePanel = document.getElementById("panelCompliance");
  confirmPanel.classList.toggle("hidden", c.status !== "under_review");
  docsPanel.classList.toggle(
    "hidden",
    !["confirmed_fraud", "ready_for_report", "reported", "closed"].includes(c.status)
  );
  reportPanel.classList.toggle(
    "hidden",
    !["confirmed_fraud", "ready_for_report", "reported", "closed"].includes(c.status)
  );
  compliancePanel.classList.toggle(
    "hidden",
    !["reported", "closed"].includes(c.status)
  );

  if (c.fraud_type) {
    const sel = document.getElementById("fraudType");
    if (sel) sel.value = c.fraud_type;
  }

  const payCb = document.getElementById("paymentBlocked");
  const payNote = document.getElementById("paymentNote");
  if (payCb) payCb.checked = !!c.payment_blocked;
  if (payNote) payNote.value = c.payment_blocked_note || "";

  const done = c.bank_checks_done || 0;
  const total = c.bank_checks_total || 0;
  document.getElementById("checkProgress").textContent = `${done}/${total}`;
  const pending = (c.bank_checks || []).filter((i) => i.status === "pending");
  const hint = document.getElementById("checkGateHint");
  if (pending.length) {
    hint.textContent = `Noch offen: ${pending.map((p) => p.entity_label).join(", ")} — bitte Schritt für Schritt abarbeiten.`;
  } else if (total === 0) {
    hint.textContent = "Noch keine Checklisten-Einträge (nach Bestätigung werden sie erzeugt).";
  } else {
    hint.textContent = "Checkliste vollständig — Report kann erzeugt werden.";
  }

  if (!docsPanel.classList.contains("hidden")) {
    renderDocsWizard(c);
  }

  const reportBtn = document.getElementById("generateReportBtn");
  const reportHint = document.getElementById("reportHint");
  const canReport = !!c.documentation_complete && ["confirmed_fraud", "ready_for_report"].includes(c.status);
  reportBtn.disabled = !canReport;
  reportBtn.classList.toggle("hidden", ["reported", "closed"].includes(c.status));
  reportBtn.title = canReport
    ? "PDF-Report erzeugen und an Compliance übergeben"
    : (pending.length
      ? `Offen: ${pending.map((p) => p.entity_label).join(", ")}`
      : "Checkliste unvollständig");
  if (reportHint) {
    if (c.status === "reported" || c.status === "closed") {
      reportHint.textContent = "Report wurde erzeugt.";
    } else if (canReport) {
      reportHint.textContent = "Checkliste vollständig — Report erzeugen, dann Fall unter Compliance abschliessen.";
    } else if (pending.length) {
      reportHint.textContent = `Noch offen: ${pending.map((p) => p.entity_label).join(", ")}`;
    } else {
      reportHint.textContent = "Report erst nach vollständiger Checkliste.";
    }
  }

  const dl = document.getElementById("downloadReportBtn");
  if (c.has_report) {
    dl.href = `/api/company-cases/${CASE_ID}/report`;
    dl.classList.remove("hidden");
  } else {
    dl.classList.add("hidden");
  }

  const actions = document.getElementById("complianceActions");
  const doneEl = document.getElementById("complianceDone");
  const hintEl = document.getElementById("complianceHint");
  if (c.status === "closed") {
    actions?.classList.add("hidden");
    if (doneEl) {
      doneEl.classList.remove("hidden");
      doneEl.textContent =
        `Geschlossen von ${c.compliance_actioned_by || "—"}` +
        (c.compliance_actioned_at ? ` · ${c.compliance_actioned_at}` : "") +
        (c.compliance_note ? ` — ${c.compliance_note}` : "");
    }
    if (hintEl) hintEl.textContent = "Fall ist abgeschlossen.";
  } else if (c.status === "reported") {
    actions?.classList.remove("hidden");
    doneEl?.classList.add("hidden");
    if (hintEl) {
      hintEl.textContent =
        "Report liegt vor. Notiz erfassen und «Actioned — Fall schliessen» tippen.";
    }
  }

  // Process rule A: closed cases stay readable + PDF for all logged-in roles (audit trail)
  if (c.status === "closed" || c.status === "cleared") {
    const audit = document.getElementById("caseAuditHint");
    if (!audit) {
      const p = document.createElement("p");
      p.id = "caseAuditHint";
      p.className = "fraud-help case-audit-hint";
      p.textContent =
        c.status === "closed"
          ? "Akte geschlossen — für alle Rollen weiterhin einsehbar inkl. Report (Audit-Trail)."
          : "Fall als «Kein Betrug» geschlossen — Akte bleibt im Filter einsehbar.";
      document.getElementById("caseMsg")?.after(p);
    } else {
      audit.classList.remove("hidden");
    }
  } else {
    document.getElementById("caseAuditHint")?.remove();
  }
}

async function loadCase() {
  const r = await fetch(`/api/company-cases/${CASE_ID}`);
  const d = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Fall nicht gefunden");
    return;
  }
  // Jump wizard to first incomplete step on fresh load
  docsWizardIndex = 0;
  const steps = docsWizardSteps(d);
  const firstOpen = steps.findIndex((s) => !s.done);
  if (firstOpen >= 0) docsWizardIndex = firstOpen;
  renderCase(d);
}

document.getElementById("confirmFraudBtn")?.addEventListener("click", async () => {
  const fraud_type = document.getElementById("fraudType")?.value;
  if (!fraud_type) {
    setMsg("Betrugsart wählen");
    return;
  }
  // Persist hit context before confirm (best effort)
  await saveHitContext({ silent: true });
  const r = await fetch(`/api/company-cases/${CASE_ID}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fraud_type }),
  });
  const d = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Fehler");
    return;
  }
  const n = d.watch_intake?.enrolled_count ?? 0;
  const skipped = d.watch_intake?.skipped_former_count ?? 0;
  const formerHint = skipped
    ? ` · ${skipped} Ehemalige nicht automatisch (optional per Watch)`
    : "";
  setMsg(`Betrug bestätigt — ${n} aktuelle Personen auf Watchlist${formerHint}, Kern-Checkliste angelegt`);
  docsWizardIndex = 0;
  renderCase(d);
});

async function saveHitContext({ silent } = {}) {
  const amountRaw = document.getElementById("hitAmount")?.value;
  const hit_amount = amountRaw === "" || amountRaw == null ? null : Number(amountRaw);
  const r = await fetch(`/api/company-cases/${CASE_ID}/hit-context`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      hit_amount: Number.isFinite(hit_amount) ? hit_amount : null,
      hit_currency: document.getElementById("hitCurrency")?.value || "CHF",
      hit_reference: document.getElementById("hitReference")?.value || null,
      hit_note: document.getElementById("hitNote")?.value || null,
    }),
  });
  const d = await r.json();
  if (!r.ok) {
    if (!silent) setMsg(formatDetail(d.detail) || "Kontext speichern fehlgeschlagen");
    return null;
  }
  if (!silent) {
    renderCase(d);
    setHitContextCollapsed(true);
    setMsg("Zahlungs-Hit gespeichert");
  }
  return d;
}

document.getElementById("saveHitContextBtn")?.addEventListener("click", () => saveHitContext());
document.getElementById("hitContextToggle")?.addEventListener("click", () => {
  const panel = document.getElementById("panelHitContext");
  if (!panel) return;
  setHitContextCollapsed(!panel.classList.contains("is-collapsed"));
});

document.getElementById("clearCaseBtn")?.addEventListener("click", async () => {
  const note = prompt("Optional: Kurznotiz") || "";
  const r = await fetch(`/api/company-cases/${CASE_ID}/clear`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note }),
  });
  const d = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Fehler");
    return;
  }
  setMsg("Fall geschlossen — kein Betrug");
  renderCase(d);
});

document.getElementById("generateReportBtn")?.addEventListener("click", async () => {
  const btn = document.getElementById("generateReportBtn");
  btn.disabled = true;
  setMsg("Report wird erzeugt…");
  try {
    const r = await fetch(`/api/company-cases/${CASE_ID}/report`, { method: "POST" });
    const d = await r.json();
    if (!r.ok) {
      setMsg(formatDetail(d.detail) || "Report fehlgeschlagen");
      btn.disabled = false;
      return;
    }
    setMsg("Report erzeugt — jetzt unter Schritt 5 abschliessen");
    renderCase(d);
    document.getElementById("panelCompliance")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    setMsg(String(e.message || e));
    btn.disabled = false;
  }
});

document.getElementById("actionCaseBtn")?.addEventListener("click", async () => {
  const note = document.getElementById("complianceNote")?.value?.trim() || "";
  if (note.length < 3) {
    setMsg("Compliance-Notiz ist Pflicht (min. 3 Zeichen)");
    return;
  }
  const btn = document.getElementById("actionCaseBtn");
  btn.disabled = true;
  try {
    const r = await fetch(`/api/company-cases/${CASE_ID}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ compliance_note: note }),
    });
    const d = await r.json();
    if (!r.ok) {
      setMsg(formatDetail(d.detail) || "Abschluss fehlgeschlagen");
      btn.disabled = false;
      return;
    }
    setMsg("Fall geschlossen");
    renderCase(d);
  } catch (e) {
    setMsg(String(e.message || e));
    btn.disabled = false;
  }
});

document.getElementById("deleteCasePageBtn")?.addEventListener("click", async () => {
  if (!confirm(`Akte #${CASE_ID} unwiderruflich löschen?`)) return;
  const r = await fetch(`/api/company-cases/${CASE_ID}`, { method: "DELETE" });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    setMsg(formatDetail(d.detail) || "Löschen fehlgeschlagen");
    return;
  }
  location.href = "/cases";
});

loadCase();
