/** CompanyCase detail wizard. */

const CASE_ID = Number(location.pathname.split("/").pop());

const STATUS_LABELS = {
  under_review: "In Prüfung",
  confirmed_fraud: "Betrug bestätigt",
  ready_for_report: "Dokumentation fertig",
  reported: "Gemeldet",
  closed: "Geschlossen",
  cleared: "Geschlossen",
};

const ENTITY_LABELS = {
  company: "Firma",
  person: "Person",
};

const COPY_ICON_SVG = `<svg class="ca-copy-svg" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
const COPY_CHECK_SVG = `<svg class="ca-copy-svg" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>`;

function d(value, kind) {
  return typeof anon === "function" ? anon(value, kind) : value;
}

let currentCase = null;
/** @type {number} */
let docsWizardIndex = 0;
/** @type {boolean | null} */
let wizPaymentChoice = null;

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((x) => x.msg || JSON.stringify(x)).join("; ");
  return detail ? String(detail) : "";
}

function setMsg(t) {
  document.getElementById("caseMsg").textContent = t || "";
}

function isSuspiciousClose(c) {
  return (c.journal || []).some((e) => String(e.text || "").includes("[In Abklärung]"));
}

function updateStepper(status) {
  // Visible steps only: review → confirm → docs (Reporting/Compliance on hold)
  const order = ["review", "confirm", "docs"];
  let active = "review";
  if (status === "cleared") active = "confirm";
  else if (status === "under_review") active = "confirm";
  else if (
    status === "confirmed_fraud"
    || status === "ready_for_report"
    || status === "reported"
    || status === "closed"
  ) {
    active = "docs";
  }

  const activeIdx = order.indexOf(active);
  document.querySelectorAll("#caseStepper .fraud-step").forEach((el) => {
    if (el.classList.contains("hidden") || el.hidden) return;
    const idx = order.indexOf(el.dataset.step);
    if (idx < 0) return;
    el.classList.toggle("is-active", idx === activeIdx);
    el.classList.toggle(
      "is-done",
      idx < activeIdx || status === "closed" || (status === "cleared" && idx <= 1)
    );
  });
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
  const panel = document.getElementById("panelHitContext");
  // Zahlungshit nur im Bestätigungsschritt (under_review)
  const show = c.status === "under_review";
  panel?.classList.toggle("hidden", !show);
  if (!show) return;

  const editable = true;
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
    summary.textContent = bits.length
      ? bits.join(" · ")
      : (hasHitContext(c) ? "Erfasst" : "Noch leer — optional ausfüllen");
  }

  form?.classList.toggle("hidden", !editable);
  saveBtn?.parentElement?.classList.toggle("hidden", !editable);
  saveBtn?.classList.toggle("hidden", !editable);
  readonly?.classList.add("hidden");
  setHitContextCollapsed(hasHitContext(c));
}

function docsWizardSteps(c) {
  const items = c.bank_checks || [];
  const steps = [
    {
      id: "payment",
      kind: "payment",
      short: "Sicherung",
      done: c.payment_blocked != null,
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
      // Journal is optional — done when checklist/Sicherung fertig or already closed
      done: (c.journal || []).length > 0
        || !!c.documentation_complete
        || c.status === "closed"
        || c.status === "ready_for_report",
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

function copyBtnHtml(text, title = "Kopieren") {
  const t = String(text || "").trim();
  if (!t) return "";
  return `<button type="button" class="ca-copy-btn" data-copy="${esc(t)}" title="${esc(title)}" aria-label="${esc(title)}">${COPY_ICON_SVG}</button>`;
}

function showCopyBubble(btn, label) {
  document.querySelectorAll(".ca-copy-bubble").forEach((el) => el.remove());
  const bubble = document.createElement("span");
  bubble.className = "ca-copy-bubble ca-copy-bubble--fixed";
  bubble.setAttribute("role", "status");
  bubble.textContent = label || "Kopiert";
  document.body.appendChild(bubble);
  const r = btn?.getBoundingClientRect?.();
  if (r && r.width) {
    bubble.style.left = `${Math.round(r.left + r.width / 2)}px`;
    bubble.style.top = `${Math.round(r.top)}px`;
  } else {
    bubble.style.left = "50%";
    bubble.style.top = "18%";
  }
  requestAnimationFrame(() => bubble.classList.add("is-on"));
  clearTimeout(bubble._hide);
  bubble._hide = setTimeout(() => {
    bubble.classList.remove("is-on");
    setTimeout(() => bubble.remove(), 180);
  }, 1600);
}

function flashCopySuccess(btn, preview) {
  if (btn) {
    btn.classList.add("is-copied");
    const prevTitle = btn.getAttribute("title") || "Kopieren";
    const hadIconOnly = btn.classList.contains("ca-copy-btn");
    if (hadIconOnly) {
      if (!btn.dataset.copyIconHtml) btn.dataset.copyIconHtml = btn.innerHTML;
      btn.innerHTML = COPY_CHECK_SVG;
    }
    btn.setAttribute("title", "Kopiert");
    clearTimeout(btn._copyFlash);
    btn._copyFlash = setTimeout(() => {
      btn.classList.remove("is-copied");
      btn.setAttribute("title", prevTitle === "Kopiert" ? "Kopieren" : prevTitle);
      if (hadIconOnly && btn.dataset.copyIconHtml) {
        btn.innerHTML = btn.dataset.copyIconHtml;
      }
    }, 1400);
  }
  const short = preview.length > 40 ? `${preview.slice(0, 37)}…` : preview;
  showCopyBubble(btn, `Kopiert · ${short}`);
}

async function copyTextToClipboard(text, btn) {
  const t = String(text || "").trim();
  if (!t) return false;
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext !== false) {
      await navigator.clipboard.writeText(t);
    } else {
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    flashCopySuccess(btn, t);
    return true;
  } catch (_) {
    setMsg("Kopieren fehlgeschlagen");
    return false;
  }
}

function bindCopyButtons(root) {
  root?.querySelectorAll("[data-copy]").forEach((btn) => {
    if (btn._copyBound) return;
    btn._copyBound = true;
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const payload = (btn.dataset.copy != null ? btn.dataset.copy : btn.getAttribute("data-copy")) || "";
      copyTextToClipboard(payload, btn);
    });
  });
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
    const selTrue = wizPaymentChoice === true || (wizPaymentChoice == null && c.payment_blocked === true);
    const selFalse = wizPaymentChoice === false || (wizPaymentChoice == null && c.payment_blocked === false);
    if (wizPaymentChoice == null && c.payment_blocked != null) {
      wizPaymentChoice = !!c.payment_blocked;
    }
    body = `
      <div class="docs-wiz-icon" aria-hidden="true">${wizardIcon("payment")}</div>
      <p class="docs-wiz-kicker">${esc(progressLabel)} · Sicherungsmassnahme</p>
      <h3 class="docs-wiz-title">Wurde die Zahlung blockiert?</h3>
      <div class="docs-wiz-answer-row" role="group" aria-label="Sicherung">
        <button type="button" class="docs-wiz-answer is-yes${selTrue ? " is-selected" : ""}" data-wiz-payment="true">
          <span class="docs-wiz-answer-ico" aria-hidden="true">✓</span>
          <span class="docs-wiz-answer-label">Ja, wurde blockiert</span>
        </button>
        <button type="button" class="docs-wiz-answer is-no${selFalse ? " is-selected" : ""}" data-wiz-payment="false">
          <span class="docs-wiz-answer-ico" aria-hidden="true">✕</span>
          <span class="docs-wiz-answer-label">Nein, konnte ausgeführt werden</span>
        </button>
      </div>
      <div class="docs-wiz-fields">
        <label class="docs-wiz-field-label" for="wizPaymentNote">Kernbanken-Referenz (optional)</label>
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
    const subjectRaw = item.entity_label || "";
    const subject = d(subjectRaw, item.entity_type === "person" ? "person" : "company");
    body = `
      <div class="docs-wiz-icon" aria-hidden="true">${wizardIcon("bank_check", item.entity_type)}</div>
      <h3 class="docs-wiz-title">Kundenbeziehung?</h3>
      <div class="docs-wiz-copy-row">
        <p class="docs-wiz-subject"><strong>${esc(subject)}</strong> · ${esc(typeLabel)}</p>
        ${copyBtnHtml(subjectRaw, "Namen kopieren")}
      </div>
      <p class="docs-wiz-copy-hint">Namen per Klick kopieren und in den Kernbanksystemen abgleichen.</p>
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
          <p class="fraud-help">${esc(item.checked_by || "")} · ${esc(formatDateTimeDisplay(item.checked_at))}${item.note ? ` — ${esc(item.note)}` : ""}</p>
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
    const canClose = !!c.documentation_complete
      && ["confirmed_fraud", "ready_for_report"].includes(c.status);
    const alreadyClosed = c.status === "closed";
    body = `
      <div class="docs-wiz-icon" aria-hidden="true">${wizardIcon("journal")}</div>
      <p class="docs-wiz-kicker">${esc(progressLabel)} · Interne Dokumentation</p>
      <h3 class="docs-wiz-title">Was wurde abgeklärt?</h3>
      <p class="docs-wiz-lead">Optional: Kurznotiz zum Ergebnis der internen Prüfung — hilft bei Prävention und Nachvollziehbarkeit, ist aber keine Pflicht.</p>
      ${journal.length ? `
        <ul class="docs-wiz-journal">${journal.map((e) => `
          <li>
            <div class="docs-wiz-journal-meta">${esc(e.author)} · ${esc(formatDateTimeDisplay(e.created_at))}</div>
            <div>${esc(e.text)}</div>
          </li>`).join("")}</ul>
      ` : `<p class="fraud-help">Noch keine Journal-Einträge (optional).</p>`}
      ${alreadyClosed ? `
        <div class="docs-wiz-done-box">
          <span class="docs-wiz-done-badge">Geschlossen</span>
          <p>Akte ist abgeschlossen.</p>
        </div>
        <div class="docs-wiz-actions">
          <button type="button" class="btn-nav docs-wiz-btn" data-wiz-prev>Zurück</button>
        </div>
      ` : `
      <div class="docs-wiz-fields">
        <label class="docs-wiz-field-label" for="wizJournalText">Neuer Eintrag (optional)</label>
        <textarea id="wizJournalText" class="fraud-net-textarea docs-wiz-input" rows="4"
          placeholder="Abklärungsergebnis (freiwillig)…" maxlength="4000"></textarea>
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
        <button type="button" class="btn-nav docs-wiz-btn" data-wiz-save-journal>
          Eintrag speichern
        </button>
        ${canClose ? `
          <button type="button" class="btn-case-equal btn-case-confirm docs-wiz-btn" data-wiz-close-case>
            Akte abschliessen
          </button>
        ` : `
          <button type="button" class="btn-case-equal btn-case-confirm docs-wiz-btn" disabled
            title="Zuerst Sicherung und alle Checklisten-Einträge erledigen">
            Akte abschliessen
          </button>
        `}
      </div>`}`;
  }

  stage.innerHTML = `<article class="docs-wiz-card">${body}</article>`;
  bindCopyButtons(stage);

  stage.querySelector("[data-wiz-prev]")?.addEventListener("click", () => {
    docsWizardIndex = Math.max(0, docsWizardIndex - 1);
    renderDocsWizard(currentCase);
  });
  stage.querySelector("[data-wiz-next]")?.addEventListener("click", () => {
    docsWizardIndex = Math.min(steps.length - 1, docsWizardIndex + 1);
    renderDocsWizard(currentCase);
  });
  stage.querySelectorAll("[data-wiz-payment]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = btn.getAttribute("data-wiz-payment");
      wizPaymentChoice = v === "true";
      stage.querySelectorAll("[data-wiz-payment]").forEach((b) => {
        b.classList.toggle("is-selected", b === btn);
      });
    });
  });
  stage.querySelector("[data-wiz-save-payment]")?.addEventListener("click", () => savePaymentFromWizard(true));
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
  stage.querySelector("[data-wiz-save-journal]")?.addEventListener("click", () => saveJournalFromWizard());
  stage.querySelector("[data-wiz-close-case]")?.addEventListener("click", () => closeCaseFromWizard());
  stage.querySelector("[data-wiz-add-check]")?.addEventListener("click", addBankCheckFromWizard);
}

async function savePaymentFromWizard(advance) {
  if (wizPaymentChoice == null && currentCase?.payment_blocked == null) {
    setMsg("Bitte wählen: blockiert oder ausgeführt");
    return;
  }
  const blocked = wizPaymentChoice != null
    ? wizPaymentChoice
    : !!currentCase.payment_blocked;
  const note = document.getElementById("wizPaymentNote")?.value || null;
  const r = await fetch(`/api/company-cases/${CASE_ID}/payment`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payment_blocked: blocked, payment_blocked_note: note }),
  });
  const data = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Fehler");
    return;
  }
  wizPaymentChoice = blocked;
  if (advance) docsWizardIndex += 1;
  renderCase(data);
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
    let data = {};
    try {
      data = await r.json();
    } catch (_) {
      data = {};
    }
    if (!r.ok) {
      showErr(formatDetail(data.detail) || `Speichern fehlgeschlagen (${r.status})`);
      if (saveBtn) saveBtn.disabled = false;
      return;
    }
    if (advance) docsWizardIndex += 1;
    renderCase(data);
    setMsg("Checklisten-Eintrag gespeichert");
  } catch (err) {
    showErr(err.message || "Speichern fehlgeschlagen");
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function saveJournalFromWizard() {
  const text = document.getElementById("wizJournalText")?.value?.trim() || "";
  if (!text) {
    setMsg("Kein Text — Journal ist optional. Zum Abschluss «Akte abschliessen» nutzen.");
    return;
  }
  if (text.length < 3) {
    setMsg("Eintrag zu kurz (mind. 3 Zeichen) — oder leer lassen und Akte abschliessen.");
    return;
  }
  const r = await fetch(`/api/company-cases/${CASE_ID}/journal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const data = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Fehler");
    return;
  }
  renderCase(data);
  setMsg("Journal-Eintrag hinzugefügt");
}

async function closeCaseFromWizard() {
  const note = document.getElementById("wizJournalText")?.value?.trim() || "";
  const go = confirm(
    "Akte abschliessen?\n\n"
    + "Dokumentation ist erfasst. Die Akte wird intern geschlossen "
    + "(ohne Reporting/Compliance)."
  );
  if (!go) return;
  const closeBtn = document.querySelector("[data-wiz-close-case]");
  if (closeBtn) closeBtn.disabled = true;
  try {
    // Optional: save journal text first if user typed something
    if (note.length >= 3) {
      const jr = await fetch(`/api/company-cases/${CASE_ID}/journal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: note }),
      });
      if (!jr.ok) {
        const jdata = await jr.json().catch(() => ({}));
        setMsg(formatDetail(jdata.detail) || "Journal speichern fehlgeschlagen");
        if (closeBtn) closeBtn.disabled = false;
        return;
      }
    }
    const r = await fetch(`/api/company-cases/${CASE_ID}/close`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: "" }),
    });
    const data = await r.json();
    if (!r.ok) {
      setMsg(formatDetail(data.detail) || "Abschluss fehlgeschlagen");
      if (closeBtn) closeBtn.disabled = false;
      return;
    }
    renderCase(data);
    setMsg("Akte geschlossen");
  } catch (err) {
    setMsg(err.message || "Abschluss fehlgeschlagen");
    if (closeBtn) closeBtn.disabled = false;
  }
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
  const data = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Hinzufügen fehlgeschlagen");
    return;
  }
  const steps = docsWizardSteps(data);
  const idx = steps.findIndex((s) => s.kind === "bank_check" && s.item && !s.done);
  docsWizardIndex = idx >= 0 ? idx : Math.max(0, steps.length - 2);
  renderCase(data);
  setMsg("Checklisten-Eintrag hinzugefügt");
}

async function maybeEnrollFormer(caseData) {
  const skipped = caseData?.watch_intake?.skipped_former_count || 0;
  const names = (caseData?.watch_intake?.skipped_former || [])
    .map((p) => p.display_name)
    .filter(Boolean)
    .slice(0, 8);
  if (!skipped) return caseData;
  const listHint = names.length ? `\n\n${names.join(", ")}${skipped > names.length ? " …" : ""}` : "";
  const go = confirm(
    `${skipped} ehemalige Organ(e) gefunden.${listHint}\n\nAuch auf Watchlist und Checkliste aufnehmen?`
  );
  if (!go) return caseData;
  const r = await fetch(`/api/company-cases/${CASE_ID}/enroll-former`, { method: "POST" });
  const data = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Ehemalige konnten nicht aufgenommen werden");
    return caseData;
  }
  const n = data.former_intake?.checklist_added ?? 0;
  setMsg(`Ehemalige aufgenommen — ${n} Checklisten-Einträge ergänzt`);
  return data;
}

function renderCase(c) {
  currentCase = c;
  document.getElementById("caseTitle").textContent = d(c.company_name, "company") || "Akte";
  let statusLabel = STATUS_LABELS[c.status] || c.status;
  if (c.status === "cleared" && isSuspiciousClose(c)) {
    statusLabel = "In Abklärung (geschlossen)";
  } else if (c.status === "cleared") {
    statusLabel = "Kein Betrug";
  }
  document.getElementById("caseSub").textContent =
    `${statusLabel}` +
    (c.company_uid ? ` · ${d(c.company_uid, "uid")}` : "") +
    ` · eröffnet von ${d(c.opened_by, "user")}`;

  if (c.status === "under_review" && c.opened_at) {
    const days = Math.floor((Date.now() - Date.parse(c.opened_at)) / 86400000);
    if (days >= 3 && !Number.isNaN(days)) {
      setMsg(`Hinweis: Akte seit ${days} Tagen in Prüfung — bitte bestätigen oder schliessen.`);
    }
  }

  updateStepper(c.status);
  renderHitContext(c);

  const confirmPanel = document.getElementById("panelConfirm");
  const docsPanel = document.getElementById("panelDocs");
  confirmPanel.classList.toggle("hidden", c.status !== "under_review");
  docsPanel.classList.toggle(
    "hidden",
    !["confirmed_fraud", "ready_for_report", "reported", "closed"].includes(c.status)
  );
  // Reporting / Compliance stay hidden (on hold)
  document.getElementById("panelReport")?.classList.add("hidden");
  document.getElementById("panelCompliance")?.classList.add("hidden");

  if (c.fraud_type) {
    const sel = document.getElementById("fraudType");
    if (sel) sel.value = c.fraud_type;
  }

  const payCb = document.getElementById("paymentBlocked");
  const payNote = document.getElementById("paymentNote");
  if (payCb) payCb.checked = !!c.payment_blocked;
  if (payNote) payNote.value = c.payment_blocked_note || "";
  if (c.payment_blocked != null) wizPaymentChoice = !!c.payment_blocked;

  const done = c.bank_checks_done || 0;
  const total = c.bank_checks_total || 0;
  document.getElementById("checkProgress").textContent = `${done}/${total}`;
  const pending = (c.bank_checks || []).filter((i) => i.status === "pending");
  const hint = document.getElementById("checkGateHint");
  if (pending.length) {
    hint.textContent = `Noch offen: ${pending.map((p) => p.entity_label).join(", ")} — bitte Schritt für Schritt abarbeiten.`;
  } else if (total === 0) {
    hint.textContent = "Noch keine Checklisten-Einträge (nach Bestätigung werden sie erzeugt).";
  } else if (c.payment_blocked == null) {
    hint.textContent = "Checkliste Personen/Firma fertig — Sicherung noch offen.";
  } else if (c.status === "closed") {
    hint.textContent = "Akte geschlossen — Dokumentation für interne Prävention abgeschlossen.";
  } else if (c.documentation_complete) {
    hint.textContent = "Dokumentation vollständig — «Akte abschliessen» im Journal-Schritt.";
  } else {
    hint.textContent = "Dokumentation läuft — offene Checklisten-Einträge abarbeiten.";
  }

  if (!docsPanel.classList.contains("hidden")) {
    renderDocsWizard(c);
  }

  if (c.status === "closed" || c.status === "cleared") {
    const audit = document.getElementById("caseAuditHint");
    const auditText = c.status === "cleared" && isSuspiciousClose(c)
      ? "Als verdächtig (In Abklärung) geschlossen — Firma und Organe auf der Watchlist; Akte bleibt einsehbar."
      : c.status === "closed"
        ? "Akte geschlossen — für alle Rollen weiterhin einsehbar (Audit-Trail)."
        : "Fall als «Kein Betrug» geschlossen — Akte bleibt im Filter einsehbar.";
    if (!audit) {
      const p = document.createElement("p");
      p.id = "caseAuditHint";
      p.className = "fraud-help case-audit-hint";
      p.textContent = auditText;
      document.getElementById("caseMsg")?.after(p);
    } else {
      audit.textContent = auditText;
      audit.classList.remove("hidden");
    }
  } else {
    document.getElementById("caseAuditHint")?.remove();
  }
}

async function loadCase() {
  const r = await fetch(`/api/company-cases/${CASE_ID}`);
  const data = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Fall nicht gefunden");
    return;
  }
  docsWizardIndex = 0;
  const steps = docsWizardSteps(data);
  const firstOpen = steps.findIndex((s) => !s.done);
  if (firstOpen >= 0) docsWizardIndex = firstOpen;
  renderCase(data);
}

document.getElementById("confirmFraudBtn")?.addEventListener("click", async () => {
  const fraud_type = document.getElementById("fraudType")?.value;
  if (!fraud_type) {
    setMsg("Betrugsart wählen");
    return;
  }
  await saveHitContext({ silent: true });
  const r = await fetch(`/api/company-cases/${CASE_ID}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fraud_type }),
  });
  const data = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Fehler");
    return;
  }
  const n = data.watch_intake?.enrolled_count ?? 0;
  setMsg(`Betrug bestätigt — ${n} aktuelle Personen auf Watchlist, Kern-Checkliste angelegt`);
  docsWizardIndex = 0;
  const afterFormer = await maybeEnrollFormer(data);
  renderCase(afterFormer);
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
  const data = await r.json();
  if (!r.ok) {
    if (!silent) setMsg(formatDetail(data.detail) || "Kontext speichern fehlgeschlagen");
    return null;
  }
  if (!silent) {
    renderCase(data);
    setHitContextCollapsed(true);
    setMsg("Zahlungs-Hit gespeichert");
  }
  return data;
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
  const data = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Fehler");
    return;
  }
  setMsg("Fall geschlossen — kein Betrug");
  renderCase(data);
});

document.getElementById("markSuspiciousBtn")?.addEventListener("click", async () => {
  const go = confirm(
    "Als verdächtig markieren?\n\n"
    + "• Tag «In Abklärung»\n"
    + "• Firma + aktuelle Organe auf die Watchlist\n"
    + "• Akte wird geschlossen"
  );
  if (!go) return;
  await saveHitContext({ silent: true });
  const r = await fetch(`/api/company-cases/${CASE_ID}/mark-suspicious`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note: "" }),
  });
  const data = await r.json();
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Markierung fehlgeschlagen");
    return;
  }
  const persons = data.watchlist?.persons_enrolled ?? 0;
  setMsg(`Als verdächtig markiert — In Abklärung, ${persons} Organe auf Watchlist, Akte geschlossen`);
  renderCase(data);
});

document.getElementById("generateReportBtn")?.addEventListener("click", async () => {
  setMsg("Reporting ist derzeit deaktiviert.");
});

document.getElementById("actionCaseBtn")?.addEventListener("click", async () => {
  setMsg("Compliance-Abschluss ist derzeit deaktiviert.");
});

document.getElementById("deleteCasePageBtn")?.addEventListener("click", async () => {
  if (!confirm(`Akte #${CASE_ID} unwiderruflich löschen?`)) return;
  const r = await fetch(`/api/company-cases/${CASE_ID}`, { method: "DELETE" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    setMsg(formatDetail(data.detail) || "Löschen fehlgeschlagen");
    return;
  }
  location.href = "/cases";
});

loadCase();
