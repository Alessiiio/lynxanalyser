/** Admin-only planning board — plain-text feature ideas (Phase 1). */
(function () {
  const STATUS_DE = {
    idea: "Idee",
    planned: "Geplant",
    building: "In Umsetzung",
    done: "Umgesetzt",
    parked: "Zurückgestellt",
  };
  const PRIORITY_DE = { low: "Niedrig", med: "Mittel", high: "Hoch" };

  let searchTimer = null;

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
    const el = document.getElementById("planMsg");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("admin-msg-err", !!isErr);
  }

  function fmtWhen(iso) {
    if (typeof formatDateTimeDisplay === "function") return formatDateTimeDisplay(iso);
    return iso || "—";
  }

  function detailOf(data) {
    const d = data && data.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
    return null;
  }

  function parseTags(raw) {
    return String(raw || "")
      .split(/[,;\s]+/)
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean);
  }

  async function api(path, options = {}) {
    let parsed;
    if (typeof fetchJson === "function") {
      parsed = await fetchJson(path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      });
    } else {
      const resp = await fetch(path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      });
      const data = await resp.json().catch(() => ({}));
      parsed = { ok: resp.ok, status: resp.status, data, resp };
    }
    if (parsed.status === 401 || parsed.status === 403 || parsed.loginRequired) {
      msg("Nur Admins — Weiterleitung…", true);
      setTimeout(() => {
        location.href = "/";
      }, 600);
      throw new Error("forbidden");
    }
    if (!parsed.ok) {
      throw new Error(detailOf(parsed.data) || `HTTP ${parsed.status}`);
    }
    return parsed.data;
  }

  function statusOptions(selected) {
    return Object.entries(STATUS_DE)
      .map(
        ([k, label]) =>
          `<option value="${esc(k)}"${k === selected ? " selected" : ""}>${esc(label)}</option>`
      )
      .join("");
  }

  function priorityOptions(selected) {
    const order = ["high", "med", "low"];
    return order
      .map(
        (k) =>
          `<option value="${esc(k)}"${k === selected ? " selected" : ""}>${esc(PRIORITY_DE[k])}</option>`
      )
      .join("");
  }

  async function copyRef(ref) {
    if (!ref) return;
    try {
      await navigator.clipboard.writeText(ref);
      msg(`${ref} kopiert`);
    } catch (_) {
      msg(`Ref: ${ref}`);
    }
  }

  function renderItem(it) {
    const id = it.id || "";
    const ref = it.ref || "";
    const st = it.status || "idea";
    const pri = it.priority || "med";
    const tags = Array.isArray(it.tags) ? it.tags : [];
    const when = fmtWhen(it.updated_at || it.created_at);
    const by = it.updated_by || it.created_by || "";
    const building = st === "building";
    const tagsVal = tags.join(", ");
    return `<li class="plan-item plan-pri-${esc(pri)}${building ? " is-building" : ""}" data-id="${esc(id)}" data-status="${esc(st)}">
      <div class="plan-item-head">
        <button type="button" class="plan-ref" title="Kurz-ID kopieren" data-ref="${esc(ref)}">${esc(ref || "—")}</button>
        ${building ? `<span class="plan-building-badge">In Umsetzung</span>` : ""}
        <input class="plan-item-title" type="text" maxlength="200" value="${esc(it.title || "")}" aria-label="Titel">
        <select class="plan-item-priority ca-select" aria-label="Priorität">${priorityOptions(pri)}</select>
        <select class="plan-item-status ca-select" aria-label="Status">${statusOptions(st)}</select>
        <span class="plan-item-meta">${esc(when)}${by ? ` · ${esc(by)}` : ""}</span>
      </div>
      <label class="plan-item-tags-label">
        <span>Tags</span>
        <input class="plan-item-tags" type="text" maxlength="200" value="${esc(tagsVal)}" placeholder="shab, ux, bank…" aria-label="Tags">
      </label>
      <textarea class="plan-item-body" rows="4" maxlength="20000" aria-label="Notizen">${esc(it.body || "")}</textarea>
      <div class="plan-item-actions">
        <button type="button" class="btn-nav plan-save">Speichern</button>
        <button type="button" class="btn-nav plan-delete">Löschen</button>
      </div>
    </li>`;
  }

  function queryString() {
    const qs = new URLSearchParams();
    const status = document.getElementById("planFilterStatus")?.value || "";
    const priority = document.getElementById("planFilterPriority")?.value || "";
    const tag = document.getElementById("planFilterTag")?.value?.trim() || "";
    const q = document.getElementById("planSearch")?.value?.trim() || "";
    if (status) qs.set("status", status);
    if (priority) qs.set("priority", priority);
    if (tag) qs.set("tag", tag);
    if (q) qs.set("q", q);
    const s = qs.toString();
    return s ? `?${s}` : "";
  }

  async function loadList() {
    msg("");
    try {
      const data = await api(`/api/admin/planning${queryString()}`);
      const items = data.items || [];
      const list = document.getElementById("planList");
      const empty = document.getElementById("planEmpty");
      const count = document.getElementById("planCount");
      if (count) count.textContent = String(items.length);
      if (!items.length) {
        list.innerHTML = "";
        empty?.classList.remove("hidden");
        return;
      }
      empty?.classList.add("hidden");
      list.innerHTML = items.map(renderItem).join("");
      wireItems(list);
    } catch (err) {
      if (err.message !== "forbidden") msg(err.message || "Laden fehlgeschlagen", true);
    }
  }

  function wireItems(root) {
    root.querySelectorAll(".plan-item").forEach((li) => {
      const id = li.dataset.id;
      li.querySelector(".plan-ref")?.addEventListener("click", () => {
        copyRef(li.querySelector(".plan-ref")?.dataset?.ref);
      });
      li.querySelector(".plan-save")?.addEventListener("click", async () => {
        const title = li.querySelector(".plan-item-title")?.value || "";
        const body = li.querySelector(".plan-item-body")?.value || "";
        const status = li.querySelector(".plan-item-status")?.value || "idea";
        const priority = li.querySelector(".plan-item-priority")?.value || "med";
        const tags = parseTags(li.querySelector(".plan-item-tags")?.value);
        try {
          await api(`/api/admin/planning/${encodeURIComponent(id)}`, {
            method: "PATCH",
            body: JSON.stringify({ title, body, status, priority, tags }),
          });
          msg("Gespeichert.");
          await loadList();
        } catch (err) {
          if (err.message !== "forbidden") msg(err.message, true);
        }
      });
      li.querySelector(".plan-delete")?.addEventListener("click", async () => {
        if (!confirm("Eintrag wirklich löschen?")) return;
        try {
          await api(`/api/admin/planning/${encodeURIComponent(id)}`, { method: "DELETE" });
          msg("Gelöscht.");
          await loadList();
        } catch (err) {
          if (err.message !== "forbidden") msg(err.message, true);
        }
      });
      li.querySelector(".plan-item-status")?.addEventListener("change", async (e) => {
        const status = e.target.value;
        try {
          await api(`/api/admin/planning/${encodeURIComponent(id)}`, {
            method: "PATCH",
            body: JSON.stringify({ status }),
          });
          msg(`Status → ${STATUS_DE[status] || status}`);
          await loadList();
        } catch (err) {
          if (err.message !== "forbidden") msg(err.message, true);
        }
      });
      li.querySelector(".plan-item-priority")?.addEventListener("change", async (e) => {
        const priority = e.target.value;
        try {
          await api(`/api/admin/planning/${encodeURIComponent(id)}`, {
            method: "PATCH",
            body: JSON.stringify({ priority }),
          });
          msg(`Priorität → ${PRIORITY_DE[priority] || priority}`);
          await loadList();
        } catch (err) {
          if (err.message !== "forbidden") msg(err.message, true);
        }
      });
    });
  }

  function wireCreate() {
    const form = document.getElementById("planCreateForm");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      // Explicit field reads (not only FormData) so status/priority/tags always stick
      const titleEl = form.querySelector('[name="title"]');
      const bodyEl = form.querySelector('[name="body"]');
      const statusEl =
        document.getElementById("planCreateStatus") || form.querySelector('[name="status"]');
      const priorityEl =
        document.getElementById("planCreatePriority") || form.querySelector('[name="priority"]');
      const tagsEl =
        document.getElementById("planCreateTags") || form.querySelector('[name="tags"]');
      const payload = {
        title: String(titleEl?.value || "").trim(),
        body: String(bodyEl?.value || "").trim(),
        status: String(statusEl?.value || "idea"),
        priority: String(priorityEl?.value || "med"),
        tags: parseTags(tagsEl?.value),
      };
      if (payload.title.length < 2) {
        msg("Titel zu kurz", true);
        return;
      }
      const btn = form.querySelector('button[type="submit"]');
      if (btn) btn.disabled = true;
      try {
        const data = await api("/api/admin/planning", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        form.reset();
        if (statusEl) statusEl.value = "idea";
        if (priorityEl) priorityEl.value = "med";
        if (tagsEl) tagsEl.value = "";
        const ref = data?.item?.ref;
        const pri = data?.item?.priority;
        const tags = Array.isArray(data?.item?.tags) ? data.item.tags.join(", ") : "";
        msg(
          ref
            ? `Gespeichert als ${ref}` +
              (pri ? ` · ${PRIORITY_DE[pri] || pri}` : "") +
              (tags ? ` · ${tags}` : "")
            : "Idee gespeichert."
        );
        await loadList();
      } catch (err) {
        if (err.message !== "forbidden") msg(err.message, true);
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  function scheduleSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadList(), 220);
  }

  async function init() {
    if (window.__lynxUser && window.__lynxUser.role !== "admin") {
      msg("Nur Admins.", true);
      location.href = "/";
      return;
    }
    wireCreate();
    document.getElementById("planReloadBtn")?.addEventListener("click", () => loadList());
    document.getElementById("planFilterStatus")?.addEventListener("change", () => loadList());
    document.getElementById("planFilterPriority")?.addEventListener("change", () => loadList());
    document.getElementById("planFilterTag")?.addEventListener("input", scheduleSearch);
    document.getElementById("planFilterTag")?.addEventListener("change", () => loadList());
    document.getElementById("planSearch")?.addEventListener("input", scheduleSearch);
    const boot = () => loadList();
    if (window.__lynxUser) boot();
    else
      window.onLynxUserReady = (u) => {
        if (u?.role !== "admin") {
          location.href = "/";
          return;
        }
        boot();
      };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
