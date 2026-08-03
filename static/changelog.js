/** Changelog page — Keep a Changelog via /api/changelog (CHANGELOG.md). */
(function () {
  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatDetail(detail) {
    if (detail == null) return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (typeof d === "string" ? d : d?.msg || JSON.stringify(d)))
        .join("; ");
    }
    try {
      return JSON.stringify(detail);
    } catch (_) {
      return String(detail);
    }
  }

  async function load() {
    const msg = document.getElementById("changelogMsg");
    const list = document.getElementById("changelogList");
    const count = document.getElementById("changelogCount");
    if (msg) msg.textContent = "Lade…";
    try {
      const resp = await fetch("/api/changelog", { credentials: "same-origin" });
      let data = {};
      try {
        data = await resp.json();
      } catch (_) {
        data = {};
      }
      if (resp.status === 401) {
        location.href = "/login";
        return;
      }
      if (!resp.ok) throw new Error(formatDetail(data.detail) || `HTTP ${resp.status}`);

      const releases = Array.isArray(data.releases) ? data.releases : [];
      if (count) count.textContent = String(releases.length);
      if (msg) {
        msg.textContent = data.source
          ? `Quelle: ${data.source}`
          : "";
      }

      if (!releases.length) {
        list.innerHTML = `<li class="fraud-help">Noch keine Versionen in CHANGELOG.md.</li>`;
        return;
      }

      const sectionOrder = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"];
      list.innerHTML = releases
        .map((rel) => {
          const sections = rel.sections || {};
          const blocks = sectionOrder
            .filter((k) => (sections[k] || []).length)
            .map((k) => {
              const lis = (sections[k] || [])
                .map((b) => `<li>${esc(b)}</li>`)
                .join("");
              return `<h4 class="changelog-section">${esc(k)}</h4><ul class="changelog-bullets">${lis}</ul>`;
            })
            .join("");
          const dateBit = rel.date ? `<time datetime="${esc(rel.date)}">${esc(formatDateDisplay(rel.date))}</time>` : "";
          return `<li class="changelog-item">
            <div class="changelog-meta">
              <strong class="changelog-ver">[${esc(rel.version)}]</strong>
              ${dateBit}
            </div>
            ${blocks || `<p class="changelog-body">Keine Einträge.</p>`}
          </li>`;
        })
        .join("");
    } catch (err) {
      if (msg) msg.textContent = err.message || "Laden fehlgeschlagen";
      if (list) list.innerHTML = "";
      if (count) count.textContent = "0";
    }
  }

  load();
})();
