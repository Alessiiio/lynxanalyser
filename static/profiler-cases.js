/** Admin page: list open/closed Profiler cases from localStorage. */
(function () {
  const PROFILER_KEY = "lynx_profiler_snips";

  const WF_LABELS = {
    payment_hit: "Zahlung",
    owner_watchlist: "Watchlist",
    network_expanded: "Netzwerk",
    customer_hit_person: "KB Person",
    customer_hit_org: "KB Org",
    aml_freeze: "AML",
  };

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function isAdmin() {
    return window.__lynxUser?.role === "admin";
  }

  function loadSnips() {
    try {
      const raw = JSON.parse(localStorage.getItem(PROFILER_KEY) || "[]");
      return Array.isArray(raw) ? raw : [];
    } catch (_) {
      return [];
    }
  }

  function saveSnips(list) {
    localStorage.setItem(PROFILER_KEY, JSON.stringify(list));
  }

  function workflowBadges(wf) {
    if (!wf) return "";
    return Object.entries(WF_LABELS)
      .filter(([k]) => wf[k])
      .map(([, label]) => `<span class="profiler-case-badge">${esc(label)}</span>`)
      .join("");
  }

  function accountCount(s) {
    return (s.entities || []).reduce(
      (n, e) => n + (e.accounts || []).filter((a) => a.clearing).length,
      0
    );
  }

  function openUrl(s) {
    const qs = new URLSearchParams();
    if (s.id) qs.set("snip", s.id);
    if (s.seed_name) qs.set("company", s.seed_name);
    if (s.seed_uid) qs.set("uid", s.seed_uid);
    return `/profiler?${qs.toString()}`;
  }

  function render() {
    const list = document.getElementById("pcList");
    const empty = document.getElementById("pcEmpty");
    const msg = document.getElementById("pcMsg");
    if (!list) return;

    if (!isAdmin()) {
      list.innerHTML = "";
      empty?.classList.remove("hidden");
      if (empty) empty.textContent = "Nur für Admins.";
      if (msg) msg.textContent = "";
      return;
    }

    const filter = document.getElementById("pcStatusFilter")?.value ?? "open";
    let snips = loadSnips();
    if (filter === "open") snips = snips.filter((s) => (s.status || "open") !== "closed");
    if (filter === "closed") snips = snips.filter((s) => s.status === "closed");

    if (!snips.length) {
      list.innerHTML = "";
      empty?.classList.remove("hidden");
      if (empty) {
        empty.textContent =
          filter === "closed"
            ? "Keine geschlossenen Fälle."
            : "Keine offenen Profiler-Fälle. In der Firmenanalyse «Profiler» starten und speichern.";
      }
      return;
    }
    empty?.classList.add("hidden");

    list.innerHTML = snips
      .map((s) => {
        const status = s.status === "closed" ? "closed" : "open";
        const when = formatDateTimeDisplay(s.updated_at || s.created_at);
        const aml = s.workflow?.aml_since
          ? ` · AML seit ${esc(s.workflow.aml_since)}`
          : "";
        return `<li class="profiler-case-card" data-id="${esc(s.id)}">
          <div class="profiler-case-main">
            <div class="profiler-case-title-row">
              <strong>${esc(s.seed_name || "Ohne Name")}</strong>
              <span class="profiler-case-status is-${status}">${status === "open" ? "Offen" : "Geschlossen"}</span>
            </div>
            <p class="profiler-case-meta">
              ${esc(s.seed_uid || "—")} · ${esc(when)} · ${esc(s.by || "")}
              · ${(s.entities || []).length} Knoten · ${accountCount(s)} Konten${aml}
            </p>
            <div class="profiler-case-badges">${workflowBadges(s.workflow) || '<span class="fraud-help">Keine Signale markiert</span>'}</div>
          </div>
          <div class="profiler-case-actions">
            <a class="btn-nav" href="${esc(openUrl(s))}">Öffnen</a>
            ${
              status === "open"
                ? `<button type="button" class="btn-nav" data-close="${esc(s.id)}">Schliessen</button>`
                : `<button type="button" class="btn-nav" data-reopen="${esc(s.id)}">Wieder öffnen</button>`
            }
            <button type="button" class="ca-tool-link" data-del="${esc(s.id)}" title="Löschen">✕</button>
          </div>
        </li>`;
      })
      .join("");

    list.querySelectorAll("[data-close]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const all = loadSnips();
        const hit = all.find((s) => s.id === btn.dataset.close);
        if (hit) {
          hit.status = "closed";
          hit.updated_at = new Date().toISOString();
          saveSnips(all);
        }
        render();
      });
    });
    list.querySelectorAll("[data-reopen]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const all = loadSnips();
        const hit = all.find((s) => s.id === btn.dataset.reopen);
        if (hit) {
          hit.status = "open";
          hit.updated_at = new Date().toISOString();
          saveSnips(all);
        }
        render();
      });
    });
    list.querySelectorAll("[data-del]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!confirm("Profiler-Fall unwiderruflich löschen?")) return;
        saveSnips(loadSnips().filter((s) => s.id !== btn.dataset.del));
        render();
      });
    });
  }

  function gate() {
    if (!isAdmin()) {
      document.getElementById("pcMsg").textContent = "Zugriff nur für Admins — Weiterleitung…";
      setTimeout(() => {
        location.href = "/";
      }, 800);
      return;
    }
    render();
  }

  document.getElementById("pcStatusFilter")?.addEventListener("change", render);

  const prev = window.onLynxUserReady;
  window.onLynxUserReady = function (u) {
    if (typeof prev === "function") prev(u);
    gate();
  };

  if (window.__lynxUser) gate();
})();
