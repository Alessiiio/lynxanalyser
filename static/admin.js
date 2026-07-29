/** Admin panel — settings + users. */
(function () {
  function esc(s) {
    return typeof escHtml === "function"
      ? escHtml(s)
      : String(s ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
  }

  function msg(text, isErr) {
    const el = document.getElementById("adminMsg");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("admin-msg-err", !!isErr);
  }

  function isAdmin() {
    return window.__lynxUser?.role === "admin";
  }

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
        ? `Zuletzt: ${(m.updated_at || "").slice(0, 19).replace("T", " ")} · ${m.updated_by || "—"}`
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
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
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

  async function loadUsers() {
    const list = document.getElementById("adminUserList");
    if (!list) return;
    const resp = await fetch("/api/users");
    const data = await resp.json();
    if (!resp.ok) {
      list.innerHTML = `<li class="fraud-help">${esc(data.detail || "Fehler")}</li>`;
      return;
    }
    const users = data.users || [];
    list.innerHTML = users
      .map((u) => {
        const roleLabel = u.role_label || u.role;
        const roleClass = u.role === "admin" ? "nav-role-admin" : "";
        return `<li class="admin-user-card">
          <div>
            <strong>${esc(u.display_name || u.username)}</strong>
            <span class="fraud-help">${esc(u.username)} · <span class="${roleClass}">${esc(roleLabel)}</span>${u.active === false ? " · inaktiv" : ""}</span>
          </div>
          <button type="button" class="btn-nav" data-reset="${u.id}" data-name="${esc(u.username)}">Passwort reset</button>
        </li>`;
      })
      .join("");
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
          msg(d.detail || "Reset fehlgeschlagen", true);
          return;
        }
        msg(`Passwort für ${btn.dataset.name} gesetzt.`);
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
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
        form.reset();
        msg(`Benutzer ${data.user?.username || ""} angelegt.`);
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
    try {
      const data = await loadSettings();
      if (!data) return;
      wireAnonToggle(data.settings || {}, data.meta || {});
      renderRuntime(data.runtime);
      await loadUsers();
      wireCreateUser();
      document.getElementById("adminRefreshUsers")?.addEventListener("click", loadUsers);
    } catch (err) {
      msg(err.message || "Admin-Panel Fehler", true);
    }
  }

  const prev = window.onLynxUserReady;
  window.onLynxUserReady = function (u) {
    if (typeof prev === "function") prev(u);
    init();
  };
  if (window.__lynxUser) init();
})();
