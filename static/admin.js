/** Admin hub — tabs: Übersicht | Exporte | Betrieb | Benutzer | Audit | System. */
(function () {
  const TAB_IDS = ["overview", "exports", "ops", "users", "audit", "system"];

  function esc(s) {
    return typeof escHtml === "function"
      ? escHtml(s)
      : String(s ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
  }

  function detailMsg(data) {
    if (!data) return "Fehler";
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
    }
    return data.detail || "Fehler";
  }

  function msg(text, isErr) {
    const el = document.getElementById("adminMsg");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("admin-msg-err", !!isErr);
  }

  function opsMsg(text, isErr) {
    const el = document.getElementById("opsMsg");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("admin-msg-err", !!isErr);
  }

  function isAdmin() {
    return window.__lynxUser?.role === "admin";
  }

  function tabFromLocation() {
    const hash = (location.hash || "").replace(/^#/, "").trim().toLowerCase();
    if (TAB_IDS.includes(hash)) return hash;
    try {
      const q = new URLSearchParams(location.search).get("tab");
      if (q && TAB_IDS.includes(q.toLowerCase())) return q.toLowerCase();
    } catch (_) {
      /* ignore */
    }
    return "overview";
  }

  function setActiveTab(tabId, { pushHash = true } = {}) {
    const id = TAB_IDS.includes(tabId) ? tabId : "overview";
    document.querySelectorAll(".admin-tab").forEach((btn) => {
      const on = btn.dataset.tab === id;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".admin-tab-panel").forEach((panel) => {
      const on = panel.dataset.panel === id;
      panel.classList.toggle("is-active", on);
      if (on) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
    });
    if (pushHash) {
      const next = `#${id}`;
      if (location.hash !== next) {
        history.replaceState(null, "", next);
      }
    }
    if (id === "overview") loadOverview();
    if (id === "users") loadUsers();
    if (id === "audit") loadAudit();
    if (id === "system") loadSystem();
  }

  function wireTabs() {
    document.getElementById("adminTabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-tab]");
      if (!btn) return;
      setActiveTab(btn.dataset.tab);
    });
    document.querySelectorAll("[data-goto]").forEach((el) => {
      el.addEventListener("click", () => setActiveTab(el.dataset.goto));
    });
    window.addEventListener("hashchange", () => {
      setActiveTab(tabFromLocation(), { pushHash: false });
    });
  }

  /* ——— Übersicht ——— */
  async function loadOverview() {
    const fraud = document.getElementById("statFraud");
    const persons = document.getElementById("statPersons");
    const companies = document.getElementById("statCompanies");
    const alerts = document.getElementById("statAlerts");
    try {
      const resp = await fetch("/api/admin/overview");
      if (resp.status === 401 || resp.status === 403) {
        msg("Nur Admins — Weiterleitung…", true);
        setTimeout(() => {
          location.href = "/";
        }, 700);
        return;
      }
      if (!resp.ok) throw new Error("Übersicht laden fehlgeschlagen");
      const data = await resp.json();
      if (fraud) fraud.textContent = String(data.fraud_cases_active ?? "—");
      if (persons) persons.textContent = String(data.watched_persons ?? "—");
      if (companies) companies.textContent = String(data.watched_companies ?? "—");
      if (alerts) alerts.textContent = String(data.open_alerts ?? "—");
    } catch (err) {
      msg(err.message || "Übersicht Fehler", true);
    }
  }

  /* ——— Exporte ——— */
  function wireExports() {
    document.querySelectorAll(".admin-export-card a[href*='/api/admin/exports/']").forEach((a) => {
      a.addEventListener("click", () => {
        msg("Export startet… (Audit wird geloggt)");
      });
    });
  }

  /* ——— Betrieb ——— */
  function wireOps() {
    document.getElementById("opsHighScan")?.addEventListener("click", async () => {
      const btn = document.getElementById("opsHighScan");
      if (btn) btn.disabled = true;
      opsMsg("Priorisierte Prüfung läuft…");
      try {
        const r = await fetch("/api/watched-persons/run-high-priority-monitoring", {
          method: "POST",
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(detailMsg(d) || `HTTP ${r.status}`);
        const sel = d.selection || {};
        const emailBit =
          d.email && d.email.sent
            ? " · Digest-E-Mail gesendet"
            : d.email && d.email.reason === "no_alerts"
              ? " · keine neuen Funde"
              : "";
        opsMsg(
          `Priorisierte geprüft: ${d.scanned || 0} (high ${sel.high_priority_selected ?? "—"}) · ` +
            `${d.new_links || 0} neue Firmen · ${d.alerts || 0} Alerts${emailBit}`
        );
      } catch (err) {
        opsMsg(err.message || "High-Prio-Scan fehlgeschlagen", true);
      } finally {
        if (btn) btn.disabled = false;
      }
    });

    document.getElementById("opsShabDaily")?.addEventListener("click", async () => {
      const btn = document.getElementById("opsShabDaily");
      if (btn) btn.disabled = true;
      opsMsg("SHAB-Tagesarchiv wird geholt…");
      try {
        const r = await fetch("/api/shab-daily/run", { method: "POST" });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(detailMsg(d) || `HTTP ${r.status}`);
        if (d.skipped) {
          opsMsg(`SHAB übersprungen: ${d.reason || d.hint || "deaktiviert"}`);
          return;
        }
        if (d.error) throw new Error(d.error);
        const fetched = d.fetched ?? d.upsert?.upserted;
        const alerts = d.match?.alerts;
        opsMsg(
          `SHAB-Lauf fertig` +
            (d.window_start && d.window_end ? ` · ${d.window_start}–${d.window_end}` : "") +
            (fetched != null ? ` · ${fetched} Publikationen` : "") +
            (alerts != null ? ` · ${alerts} Alerts` : "")
        );
      } catch (err) {
        opsMsg(err.message || "SHAB-Lauf fehlgeschlagen", true);
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  /* ——— System ——— */
  async function loadSettings() {
    const resp = await fetch("/api/admin/settings");
    if (resp.status === 403 || resp.status === 401) {
      msg("Nur Admins — Weiterleitung…", true);
      setTimeout(() => {
        location.href = "/";
      }, 700);
      return null;
    }
    if (!resp.ok) throw new Error("Einstellungen laden fehlgeschlagen");
    return resp.json();
  }

  function renderRuntime(rt) {
    const el = document.getElementById("adminRuntime");
    if (!el || !rt) return;
    const rows = [
      ["Umgebung", rt.environment],
      ["Cache TTL (s)", rt.cache_ttl_seconds],
      ["Rate-Limit / Min", rt.rate_limit_per_minute],
      ["HTTPS-only Cookies", rt.https_only ? "ja" : "nein"],
      ["Zefix", rt.zefix_configured ? "konfiguriert" : "fehlt"],
      ["VirusTotal", rt.virustotal_configured ? "konfiguriert" : "fehlt"],
      ["Safe Browsing", rt.safebrowsing_configured ? "konfiguriert" : "fehlt"],
      ["URLScan", rt.urlscan_configured ? "konfiguriert" : "fehlt"],
      ["Ollama", rt.ollama_configured ? "konfiguriert" : "fehlt"],
      ["Anthropic", rt.anthropic_configured ? "konfiguriert" : "fehlt"],
    ];
    el.innerHTML = rows
      .map(
        ([k, v]) =>
          `<div class="admin-runtime-row"><dt>${esc(k)}</dt><dd>${esc(String(v))}</dd></div>`
      )
      .join("");
  }

  function wireAnonToggle(settings, meta) {
    const toggle = document.getElementById("adminAnonToggle");
    const metaEl = document.getElementById("adminAnonMeta");
    if (!toggle) return;
    toggle.checked = !!settings.anonymize_mode;
    if (typeof applyAnonymizeMode === "function") {
      applyAnonymizeMode(!!settings.anonymize_mode, { silent: true });
    }
    const m = meta?.anonymize_mode;
    if (metaEl) {
      metaEl.textContent = m?.updated_at
        ? `Zuletzt: ${formatDateTimeDisplay(m.updated_at)} · ${m.updated_by || "—"}`
        : "Noch nie gesetzt — Standard: aus.";
    }
    toggle.onchange = async () => {
      toggle.disabled = true;
      try {
        const resp = await fetch("/api/admin/settings", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ anonymize_mode: toggle.checked }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(detailMsg(data) || `HTTP ${resp.status}`);
        if (typeof applyAnonymizeMode === "function") {
          applyAnonymizeMode(!!data.settings?.anonymize_mode);
        }
        if (typeof renderSiteNav === "function") renderSiteNav();
        msg(
          toggle.checked
            ? "Anonymisierungsmodus aktiv — gilt für alle Benutzer."
            : "Anonymisierungsmodus aus."
        );
        const refreshed = await loadSettings();
        if (refreshed) {
          wireAnonToggle(refreshed.settings, refreshed.meta);
        }
      } catch (err) {
        toggle.checked = !toggle.checked;
        msg(err.message || "Speichern fehlgeschlagen", true);
      } finally {
        toggle.disabled = false;
      }
    };
  }

  async function loadSystem() {
    try {
      const data = await loadSettings();
      if (!data) return;
      wireAnonToggle(data.settings || {}, data.meta || {});
      renderRuntime(data.runtime);
    } catch (err) {
      msg(err.message || "System laden fehlgeschlagen", true);
    }
  }

  /* ——— Audit ——— */
  async function loadAudit() {
    const list = document.getElementById("auditList");
    const filter = document.getElementById("auditActionFilter");
    const exportLink = document.getElementById("auditExportCsv");
    if (!list) return;
    const action = (filter?.value || "").trim();
    if (exportLink) {
      exportLink.href = action
        ? `/api/admin/audit/export.csv?action=${encodeURIComponent(action)}`
        : "/api/admin/audit/export.csv";
    }
    list.innerHTML = `<p class="fraud-help">Lade…</p>`;
    try {
      const qs = new URLSearchParams({ limit: "100" });
      if (action) qs.set("action", action);
      const resp = await fetch(`/api/admin/audit?${qs}`);
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(detailMsg(data) || `HTTP ${resp.status}`);
      const items = data.items || [];
      if (!items.length) {
        list.innerHTML = `<p class="fraud-help">Keine Einträge.</p>`;
        return;
      }
      list.innerHTML = items
        .map((ev) => {
          const ok = ev.success !== false;
          const when =
            typeof formatDateTimeDisplay === "function"
              ? formatDateTimeDisplay(ev.created_at)
              : ev.created_at || "—";
          const who = ev.actor_display || ev.actor_username || "—";
          const target = ev.target ? ` · ${esc(ev.target)}` : "";
          const detail = ev.detail ? `<span class="admin-audit-detail">${esc(ev.detail)}</span>` : "";
          return `<article class="admin-audit-row${ok ? "" : " is-fail"}">
            <div class="admin-audit-meta">
              <time datetime="${esc(ev.created_at || "")}">${esc(when)}</time>
              <span class="admin-audit-action">${esc(ev.action || "")}</span>
              ${ok ? "" : '<span class="admin-audit-fail">fehlgeschlagen</span>'}
            </div>
            <div class="admin-audit-body">
              <strong>${esc(who)}</strong>${target}
              ${ev.ip ? `<span class="fraud-help"> · ${esc(ev.ip)}</span>` : ""}
              ${detail}
            </div>
          </article>`;
        })
        .join("");
      if (data.total != null && data.total > items.length) {
        list.insertAdjacentHTML(
          "beforeend",
          `<p class="fraud-help">Zeige ${items.length} von ${data.total} — CSV für mehr.</p>`
        );
      }
    } catch (err) {
      list.innerHTML = `<p class="fraud-help admin-msg-err">${esc(err.message || "Audit laden fehlgeschlagen")}</p>`;
    }
  }

  function wireAudit() {
    document.getElementById("auditRefresh")?.addEventListener("click", loadAudit);
    document.getElementById("auditActionFilter")?.addEventListener("change", loadAudit);
    document.getElementById("auditExportCsv")?.addEventListener("click", () => {
      msg("Audit-Export startet…");
    });
  }

  /* ——— Benutzer (CRUD) ——— */
  const ROLE_OPTIONS = [
    ["case_manager", "Case Manager"],
    ["compliance", "Compliance"],
    ["admin", "Admin"],
  ];

  async function patchUser(id, body) {
    const r = await fetch(`/api/users/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(detailMsg(d));
    return d.user;
  }

  async function loadUsers() {
    const list = document.getElementById("adminUserList");
    if (!list) return;
    const resp = await fetch("/api/users");
    const data = await resp.json();
    if (!resp.ok) {
      list.innerHTML = `<li class="fraud-help">${esc(detailMsg(data))}</li>`;
      return;
    }
    const meId = window.__lynxUser?.id;
    const users = data.users || [];
    list.innerHTML = users
      .map((u) => {
        const roleClass = u.role === "admin" ? "nav-role-admin" : "";
        const inactive = u.active === false;
        const roleOpts = ROLE_OPTIONS.map(
          ([v, lab]) =>
            `<option value="${v}"${u.role === v ? " selected" : ""}>${esc(lab)}</option>`
        ).join("");
        const totpBit = u.totp_enabled ? "2FA an" : "2FA aus";
        return `<li class="card admin-user-card${inactive ? " admin-user-inactive" : ""}" data-user-id="${u.id}">
          <div class="admin-user-main">
            <strong>${esc(u.display_name || u.username)}</strong>
            <span class="fraud-help">${esc(u.username)} · <span class="${roleClass}">${esc(u.role_label || u.role)}</span>${inactive ? " · inaktiv" : ""} · ${totpBit}</span>
            <label class="admin-user-role">
              <span class="fraud-help">Rolle</span>
              <select class="ca-select" data-role="${u.id}" ${inactive ? "disabled" : ""}>${roleOpts}</select>
            </label>
          </div>
          <div class="admin-user-actions">
            <button type="button" class="btn btn-sm" data-save-role="${u.id}" ${inactive ? "disabled" : ""}>Rolle speichern</button>
            ${
              inactive
                ? `<button type="button" class="btn btn-sm" data-reactivate="${u.id}" data-name="${esc(u.username)}">Reaktivieren</button>
            <button type="button" class="btn btn-sm btn-danger" data-hard-delete="${u.id}" data-name="${esc(u.username)}" ${u.id === meId ? "disabled" : ""}>Endgültig löschen</button>`
                : `<button type="button" class="btn btn-sm" data-deactivate="${u.id}" data-name="${esc(u.username)}">Deaktivieren</button>`
            }
            <button type="button" class="btn btn-sm" data-reset="${u.id}" data-name="${esc(u.username)}" ${inactive ? "disabled" : ""}>Passwort reset</button>
            <button type="button" class="btn btn-sm" data-reset-2fa="${u.id}" data-name="${esc(u.username)}" ${u.id === meId || inactive || !u.totp_enabled ? "disabled" : ""}>2FA reset</button>
          </div>
        </li>`;
      })
      .join("");

    list.querySelectorAll("[data-save-role]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.saveRole;
        const sel = list.querySelector(`select[data-role="${id}"]`);
        if (!sel) return;
        try {
          await patchUser(Number(id), { role: sel.value });
          msg(`Rolle aktualisiert.`);
          if (Number(id) === meId) {
            await loadCurrentUser();
            renderSiteNav();
          }
          loadUsers();
        } catch (ex) {
          msg(ex.message || "Rollen-Update fehlgeschlagen", true);
        }
      });
    });

    list.querySelectorAll("[data-deactivate]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Benutzer «${btn.dataset.name}» deaktivieren (Soft-Delete)?`)) return;
        try {
          await patchUser(Number(btn.dataset.deactivate), { active: false });
          msg(`«${btn.dataset.name}» deaktiviert.`);
          loadUsers();
        } catch (ex) {
          msg(ex.message || "Deaktivieren fehlgeschlagen", true);
        }
      });
    });

    list.querySelectorAll("[data-reactivate]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await patchUser(Number(btn.dataset.reactivate), { active: true });
          msg(`«${btn.dataset.name}» reaktiviert.`);
          loadUsers();
        } catch (ex) {
          msg(ex.message || "Reaktivieren fehlgeschlagen", true);
        }
      });
    });

    list.querySelectorAll("[data-hard-delete]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const name = btn.dataset.name;
        if (
          !confirm(
            `Benutzer «${name}» ENDGÜLTIG löschen?\n\nDer Account wird aus der Datenbank entfernt. Username kann danach neu vergeben werden. Nicht rückgängig machbar.`
          )
        ) {
          return;
        }
        try {
          const r = await fetch(`/api/users/${btn.dataset.hardDelete}`, { method: "DELETE" });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(detailMsg(d));
          msg(`«${name}» endgültig gelöscht.`);
          loadUsers();
        } catch (ex) {
          msg(ex.message || "Endgültiges Löschen fehlgeschlagen", true);
        }
      });
    });

    list.querySelectorAll("[data-reset]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const pw = prompt(`Neues Passwort für ${btn.dataset.name} (min. 12 Zeichen):`);
        if (!pw) return;
        if (pw.length < 12) {
          msg("Passwort zu kurz (min. 12).", true);
          return;
        }
        const r = await fetch(`/api/users/${btn.dataset.reset}/reset-password`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: pw }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          msg(detailMsg(d) || "Reset fehlgeschlagen", true);
          return;
        }
        msg(`Passwort für ${btn.dataset.name} gesetzt.`);
      });
    });

    list.querySelectorAll("[data-reset-2fa]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`2FA für «${btn.dataset.name}» zurücksetzen? Der User muss 2FA neu einrichten.`)) {
          return;
        }
        const r = await fetch(`/api/users/${btn.dataset.reset2fa}/reset-2fa`, {
          method: "POST",
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          msg(detailMsg(d) || "2FA-Reset fehlgeschlagen", true);
          return;
        }
        msg(`2FA für ${btn.dataset.name} zurückgesetzt.`);
        loadUsers();
      });
    });
  }

  function wireCreateUser() {
    document.getElementById("adminCreateUser")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.target;
      const body = Object.fromEntries(new FormData(form).entries());
      try {
        const resp = await fetch("/api/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(detailMsg(data) || `HTTP ${resp.status}`);
        form.reset();
        msg(`Benutzer ${data.user?.username || ""} angelegt (muss 2FA beim ersten Login einrichten).`);
        loadUsers();
      } catch (err) {
        msg(err.message || "Anlegen fehlgeschlagen", true);
      }
    });
  }

  async function init() {
    if (!isAdmin()) {
      msg("Nur für Admins.", true);
      setTimeout(() => {
        location.href = "/";
      }, 600);
      return;
    }
    wireTabs();
    wireExports();
    wireOps();
    wireAudit();
    wireCreateUser();
    document.getElementById("adminRefreshUsers")?.addEventListener("click", loadUsers);
    setActiveTab(tabFromLocation(), { pushHash: true });
  }

  const prev = window.onLynxUserReady;
  window.onLynxUserReady = function (u) {
    if (typeof prev === "function") prev(u);
    init();
  };
  if (window.__lynxUser) init();
})();
