/** Feedback / wishlist board page. */
(function () {
  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  const STATUS_OPTS = [
    ["open", "Offen"],
    ["reviewing", "In Prüfung"],
    ["in_progress", "In Arbeit"],
    ["done", "Erledigt"],
    ["rejected", "Abgelehnt"],
  ];

  async function load() {
    const msg = document.getElementById("feedbackPageMsg");
    const list = document.getElementById("fbList");
    const count = document.getElementById("fbCount");
    const status = document.getElementById("fbFilterStatus")?.value || "";
    const type = document.getElementById("fbFilterType")?.value || "";
    if (msg) msg.textContent = "Lade…";
    try {
      const qs = new URLSearchParams();
      if (status) qs.set("status", status);
      if (type) qs.set("type", type);
      const resp = await fetch(`/api/wishlist?${qs}`, { credentials: "same-origin" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      const items = data.items || [];
      const labels = data.labels || {};
      const isAdmin = window.__lynxUser?.role === "admin";
      count.textContent = String(items.length);
      msg.textContent = "";
      if (!items.length) {
        list.innerHTML = `<li class="fraud-help">Keine Einträge.</li>`;
        return;
      }
      list.innerHTML = items
        .map((it) => {
          const stLabel = (labels.status && labels.status[it.status]) || it.status;
          const tyLabel = (labels.type && labels.type[it.type]) || it.type;
          const adminBit = isAdmin
            ? `<label class="fb-status-edit">Status
                <select data-id="${esc(it.id)}" class="fb-status-select">
                  ${STATUS_OPTS.map(
                    ([v, lab]) =>
                      `<option value="${v}"${v === it.status ? " selected" : ""}>${lab}</option>`
                  ).join("")}
                </select>
              </label>`
            : "";
          return `<li class="changelog-item fb-item">
            <div class="changelog-meta">
              <span class="fraud-badge">${esc(tyLabel)}</span>
              <span class="fraud-speed-hint">${esc(stLabel)}</span>
              <time>${esc(formatDateDisplay(it.created_at))}</time>
              ${it.created_by ? `<span>${esc(it.created_by)}</span>` : ""}
            </div>
            <h3 class="changelog-title">${esc(it.title)}</h3>
            <p class="changelog-body">${esc(it.description || "")}</p>
            ${adminBit}
          </li>`;
        })
        .join("");

      list.querySelectorAll(".fb-status-select").forEach((sel) => {
        sel.addEventListener("change", async () => {
          const id = sel.getAttribute("data-id");
          try {
            const r = await fetch(`/api/wishlist/${id}`, {
              method: "PATCH",
              credentials: "same-origin",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ status: sel.value }),
            });
            const d = await r.json();
            if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
            load();
          } catch (err) {
            if (msg) msg.textContent = err.message || "Status-Update fehlgeschlagen";
          }
        });
      });
    } catch (err) {
      if (msg) msg.textContent = err.message || "Laden fehlgeschlagen";
      list.innerHTML = "";
      count.textContent = "0";
    }
  }

  document.getElementById("fbReloadBtn")?.addEventListener("click", load);
  document.getElementById("fbFilterStatus")?.addEventListener("change", load);
  document.getElementById("fbFilterType")?.addEventListener("change", load);

  if (window.__lynxUser) load();
  else {
    const prev = window.onLynxUserReady;
    window.onLynxUserReady = function (u) {
      if (typeof prev === "function") prev(u);
      load();
    };
  }
})();
