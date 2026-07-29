/** Profiler full-page fall-cockpit (Admin). Bank data stays in localStorage. */
(function () {
  const PROFILER_KEY = "lynx_profiler_snips";
  const params = new URLSearchParams(location.search);

  let draft = null;
  let seedCompany = null; // { name, uid, canton?, registry_office_id? }
  let hrGraph = null;
  let selectedEntityId = null;
  let activeView = null; // null = cockpit
  let profilerNetwork = null;
  let scanSourceId = null;
  let scanMatches = [];
  let pendingCompanyPick = null;
  let addSuggestTimer = null;
  const bankLookupCache = new Map();

  const WORKFLOW_FIELDS = [
    {
      key: "payment_hit",
      title: "Zahlungs-Hit",
      help: "Zahlung an die Seed-Firma auf der Akte erfassen.",
      action: { href: "/cases", label: "Zur Akte" },
    },
    {
      key: "owner_watchlist",
      title: "Inhaber auf Watchlist",
      help: "Aktuellen Inhaber/Organ auf die Personen-Watchlist setzen.",
      action: { href: "/watchlist", label: "Watchlist" },
    },
    {
      key: "network_expanded",
      title: "Weitere Firmen / Verein",
      help: "Beziehungen im HR aufdecken.",
    },
    {
      key: "customer_hit_person",
      title: "Kundenbeziehung — Person",
      help: "Interne Suche: Treffer auf die Privatperson.",
    },
    {
      key: "customer_hit_org",
      title: "Kundenbeziehung — Verein/Firma",
      help: "Interne Suche: Treffer auf verbundene Org.",
    },
    {
      key: "aml_freeze",
      title: "AML-Sperre / Früherkennung",
      help: "Sperre notieren — hätte den Fall vorziehen können.",
      date: true,
    },
  ];

  function esc(s) {
    return typeof escHtml === "function"
      ? escHtml(s)
      : String(s ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
  }

  function d(value, kind) {
    return typeof anon === "function" ? anon(value, kind) : value ?? "";
  }

  function memberLabel() {
    return window.__lynxUser?.display_name || window.__lynxUser?.username || "Team";
  }

  function isAdmin() {
    return window.__lynxUser?.role === "admin";
  }

  function msg(text) {
    const el = document.getElementById("ppMsg");
    if (el) el.textContent = text || "";
  }

  function defaultWorkflow() {
    return {
      payment_hit: false,
      owner_watchlist: false,
      network_expanded: false,
      customer_hit_person: false,
      customer_hit_org: false,
      aml_freeze: false,
      aml_since: "",
    };
  }

  function ensureWorkflow(dr) {
    if (!dr) return dr;
    dr.workflow = { ...defaultWorkflow(), ...(dr.workflow || {}) };
    dr.status = dr.status === "closed" ? "closed" : "open";
    dr.hr_edges = (dr.hr_edges || []).map((e) => ({
      from: String(e.from),
      to: String(e.to),
      kind: e.kind || "hr",
      label: e.label || (e.kind === "payment" ? "Zahlung" : ""),
      note: e.note || "",
      id: e.id || `edge-${e.from}-${e.to}-${e.kind || "hr"}`,
    }));
    return dr;
  }

  function normUid(uid) {
    return String(uid || "")
      .replace(/[^a-zA-Z0-9]/g, "")
      .toLowerCase();
  }

  function normName(name) {
    return String(name || "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .replace(/[,.\-–—]/g, " ")
      .trim();
  }

  function cantonCode(raw) {
    if (!raw) return "";
    if (typeof raw === "object") {
      return String(raw.id || raw.shortName || raw.code || "").trim().toUpperCase();
    }
    return String(raw).trim().toUpperCase();
  }

  function entityKey(e) {
    return e.type === "company"
      ? `c:${normUid(e.uid) || normName(e.label)}`
      : `p:${normName(e.label)}`;
  }

  function loadSnips() {
    try {
      const raw = JSON.parse(localStorage.getItem(PROFILER_KEY) || "[]");
      return Array.isArray(raw) ? raw : [];
    } catch (_) {
      return [];
    }
  }

  function migrateEntity(e) {
    if (!Array.isArray(e.accounts)) {
      e.accounts = [];
      if (e.clearing) {
        e.accounts.push({
          id: `acc-${e.id}-0`,
          clearing: String(e.clearing),
          bank_label: e.bank_label || "",
          bic: "",
          note: e.note || "",
        });
      }
    }
    return e;
  }

  function dedupeProfilerEntities(entities) {
    const out = [];
    const byKey = new Map();
    const remap = new Map();
    for (const e of entities) {
      const key = entityKey(e);
      if (byKey.has(key)) {
        const keep = byKey.get(key);
        remap.set(e.id, keep.id);
        keep.seed = !!(keep.seed || e.seed);
        keep.uid = keep.uid || e.uid || "";
        if (e.seed && e.label) keep.label = e.label;
        keep.accounts = [...(keep.accounts || []), ...(e.accounts || [])];
      } else {
        byKey.set(key, e);
        out.push(e);
        remap.set(e.id, e.id);
      }
    }
    return { entities: out, remap };
  }

  function migrateDraft(dr) {
    if (!dr) return null;
    dr.entities = (dr.entities || []).map(migrateEntity);
    dr.hr_edges = dr.hr_edges || [];
    const fixed = dedupeProfilerEntities(dr.entities);
    dr.entities = fixed.entities;
    dr.hr_edges = dr.hr_edges
      .map((e) => ({
        id: e.id || `edge-${e.from}-${e.to}-${e.kind || "hr"}`,
        from: fixed.remap.get(String(e.from)) || String(e.from),
        to: fixed.remap.get(String(e.to)) || String(e.to),
        kind: e.kind || "hr",
        label: e.label || "",
        note: e.note || "",
      }))
      .filter((e) => e.from && e.to && e.from !== e.to);
    return ensureWorkflow(dr);
  }

  function sameSeed(dr, company) {
    if (!dr || !company) return false;
    const uidA = normUid(dr.seed_uid);
    const uidB = normUid(company.uid);
    if (uidA && uidB && uidA === uidB) return true;
    return !!(normName(dr.seed_name) && normName(company.name) && normName(dr.seed_name) === normName(company.name));
  }

  function findOpenSnipForCompany(company) {
    if (!company) return null;
    return loadSnips().find((s) => (s.status || "open") !== "closed" && sameSeed(s, company)) || null;
  }

  function accountCount(dr) {
    return (dr?.entities || []).reduce(
      (n, e) => n + (e.accounts || []).filter((a) => a.clearing).length,
      0
    );
  }

  function workflowDone(dr) {
    const wf = dr?.workflow || {};
    return WORKFLOW_FIELDS.filter((f) => wf[f.key]).length;
  }

  function updateHeader() {
    const name = draft?.seed_name || seedCompany?.name || "";
    const uid = draft?.seed_uid || seedCompany?.uid || "";
    document.getElementById("ppSeedName").textContent = name ? d(name, "company") : "Kein Fall";
    const uidEl = document.getElementById("ppSeedUid");
    uidEl.textContent = uid ? d(uid, "uid") : "";
    const badge = document.getElementById("ppStatusBadge");
    const st = draft?.status === "closed" ? "closed" : "open";
    badge.textContent = st === "closed" ? "Geschlossen" : "Offen";
    badge.className = `pp-status is-${st}`;
    document.getElementById("ppTitle").textContent = name ? `Profiler — ${d(name, "company")}` : "Profiler";
    document.title = name ? `Profiler — ${d(name, "company")}` : "Profiler";
    const back = document.getElementById("ppBackLink");
    if (back && name) {
      const qs = new URLSearchParams();
      if (seedCompany?.name || draft?.seed_name) qs.set("company", seedCompany?.name || draft.seed_name);
      if (seedCompany?.uid || draft?.seed_uid) qs.set("uid", seedCompany?.uid || draft.seed_uid);
      back.href = qs.toString() ? `/?${qs}` : "/";
    }
    document.getElementById("ppSaveBtn").disabled = !draft?.entities?.length;
  }

  function updateCockpitCards() {
    const n = draft?.entities?.length || 0;
    document.getElementById("ppCardNetzMeta").textContent = n ? `${n} Knoten` : "Noch leer";
    document.getElementById("ppCardSigMeta").textContent = `${workflowDone(draft)} / ${WORKFLOW_FIELDS.length}`;
    document.getElementById("ppCardAccMeta").textContent = `${accountCount(draft)} Konten`;
    const manualLinks = (draft?.hr_edges || []).filter((e) => e.kind && e.kind !== "hr").length;
    const ov = document.getElementById("ppCardOverviewMeta");
    if (ov) {
      ov.textContent = manualLinks
        ? `${n} Knoten · ${manualLinks} Extra-Link${manualLinks === 1 ? "" : "s"}`
        : n
          ? `${n} Knoten`
          : "Beziehungsnetzwerk";
    }
  }

  function showCockpit() {
    activeView = null;
    document.getElementById("ppCockpit")?.classList.remove("hidden");
    document.getElementById("ppWorkspace")?.classList.add("hidden");
    updateCockpitCards();
  }

  function showView(view) {
    activeView = view;
    document.getElementById("ppCockpit")?.classList.add("hidden");
    document.getElementById("ppWorkspace")?.classList.remove("hidden");
    document.querySelectorAll("[data-pp-panel]").forEach((p) => {
      p.classList.toggle("hidden", p.dataset.ppPanel !== view);
    });
    const titles = {
      netz: "Netzwerk erweitern",
      signale: "Signale",
      konten: "Konten",
      overview: "Übersicht — Beziehungsnetzwerk",
      export: "Screening",
    };
    document.getElementById("ppWorkspaceTitle").textContent = titles[view] || view;
    document.getElementById("ppReloadNetz")?.classList.toggle("hidden", view !== "netz");

    if (view === "netz") {
      renderEntityList();
      renderEntityDetail();
    }
    if (view === "signale") renderWorkflow();
    if (view === "konten") {
      renderAccEntityList();
      renderAccEditor();
    }
    if (view === "overview") {
      renderLinkEditor();
      renderGraph();
    }
    if (view === "export") renderExport();
  }

  /* ── Snip from HR graph ── */

  function findCompanyMatch(entities, company) {
    const uid = normUid(company.uid);
    const name = normName(company.name);
    const hit = entities.find((e) => {
      if (e.type !== "company") return false;
      if (uid && normUid(e.uid) === uid) return true;
      if (name && normName(e.label) === name) return true;
      return false;
    });
    return hit || entities.find((e) => e.type === "company" && e.seed) || null;
  }

  function mergeAccountsFromPrevious(prev, nextEntities) {
    if (!prev?.entities?.length) return nextEntities;
    const byKey = new Map();
    for (const e of prev.entities) byKey.set(entityKey(e), e);
    for (const e of nextEntities) {
      const old = byKey.get(entityKey(e));
      if (old?.accounts?.length) e.accounts = JSON.parse(JSON.stringify(old.accounts));
    }
    return nextEntities;
  }

  function snipFromGraph(graph, company, prev) {
    const nodes = graph?.nodes || [];
    const edges = graph?.edges || [];
    const entities = [];
    for (const n of nodes) {
      const type = n.type === "person" || n.node_type === "person" ? "person" : "company";
      const label = String(n.name || n.label || n.id || "—").split("\n")[0].trim();
      entities.push({
        id: String(n.id || `${type}:${label}`),
        type,
        label,
        uid: n.uid || "",
        seed: !!(n.seed || n.is_seed),
        accounts: [],
      });
    }
    if (company) {
      const match = findCompanyMatch(entities, company);
      if (match) {
        match.seed = true;
        match.uid = match.uid || company.uid || "";
        match.label = company.name || match.label;
      } else {
        entities.push({
          id: `company:${company.uid || company.name}`,
          type: "company",
          label: company.name || "Kernfirma",
          uid: company.uid || "",
          seed: true,
          accounts: [],
        });
      }
    }
    const merged = mergeAccountsFromPrevious(prev, entities);
    if (prev?.entities?.length) {
      const keys = new Set(merged.map((e) => entityKey(e)));
      for (const e of prev.entities) {
        const k = entityKey(e);
        if (!keys.has(k) && (e.manual || e.note)) {
          merged.push(e);
          keys.add(k);
        }
      }
    }
    const { entities: unique, remap } = dedupeProfilerEntities(merged);
    const idSet = new Set(unique.map((e) => e.id));
    const edgeSeen = new Set();
    const hrEdges = [];
    for (const e of edges) {
      const from = remap.get(String(e.from ?? e.source)) || String(e.from ?? e.source);
      const to = remap.get(String(e.to ?? e.target)) || String(e.to ?? e.target);
      if (!from || !to || from === to || !idSet.has(from) || !idSet.has(to)) continue;
      const k = `${from}->${to}:hr`;
      if (edgeSeen.has(k)) continue;
      edgeSeen.add(k);
      hrEdges.push({ id: `hr-${from}-${to}`, from, to, kind: "hr", label: "", note: "" });
    }
    if (prev?.hr_edges?.length) {
      for (const e of prev.hr_edges) {
        const from = remap.get(String(e.from)) || String(e.from);
        const to = remap.get(String(e.to)) || String(e.to);
        const kind = e.kind || "hr";
        if (!from || !to || from === to || !idSet.has(from) || !idSet.has(to)) continue;
        const k = `${from}->${to}:${kind}`;
        if (edgeSeen.has(k)) continue;
        edgeSeen.add(k);
        hrEdges.push({
          id: e.id || `edge-${from}-${to}-${kind}`,
          from,
          to,
          kind,
          label: e.label || "",
          note: e.note || "",
        });
      }
    }
    return ensureWorkflow({
      id: prev?.id || `snip-${Date.now()}`,
      seed_name: company?.name || unique.find((e) => e.seed)?.label || "Snip",
      seed_uid: company?.uid || "",
      seed_canton: cantonCode(company?.canton || graph?.company?.canton || prev?.seed_canton || ""),
      seed_registry_office_id:
        company?.registry_office_id
        ?? graph?.company?.registry_office_id
        ?? prev?.seed_registry_office_id
        ?? null,
      created_at: prev?.created_at || new Date().toISOString(),
      updated_at: new Date().toISOString(),
      by: memberLabel(),
      status: prev?.status || "open",
      workflow: prev?.workflow || defaultWorkflow(),
      entities: unique,
      hr_edges: hrEdges,
    });
  }

  async function loadHrNetwork(company, uid) {
    const qs = new URLSearchParams();
    if (company) qs.set("company", company);
    if (uid) qs.set("uid", uid);
    const resp = await fetch(`/api/hr-network?${qs}`);
    const data = await resp.json();
    if (!resp.ok) {
      const detail = typeof data.detail === "string" ? data.detail : `HTTP ${resp.status}`;
      throw new Error(detail);
    }
    return data;
  }

  /* ── Entity ops ── */

  function addEdge(fromId, toId, { kind = "hr", label = "", note = "" } = {}) {
    if (!draft || !fromId || !toId || fromId === toId) return;
    draft.hr_edges = draft.hr_edges || [];
    const exists = draft.hr_edges.some(
      (e) => e.from === fromId && e.to === toId && (e.kind || "hr") === kind
    );
    if (exists) return;
    draft.hr_edges.push({
      id: `edge-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      from: fromId,
      to: toId,
      kind,
      label: label || (kind === "payment" ? "Zahlung" : kind === "relation" ? "Bezug" : ""),
      note: note || "",
    });
  }

  function findEntityByKey(type, label, uid) {
    const probe = { type, label, uid: uid || "" };
    return draft?.entities?.find((e) => entityKey(e) === entityKey(probe)) || null;
  }

  function upsertEntity({ type, label, uid, note, seed, linkFromId }) {
    if (!draft) {
      draft = ensureWorkflow({
        id: `snip-${Date.now()}`,
        seed_name: seedCompany?.name || label,
        seed_uid: seedCompany?.uid || "",
        created_at: new Date().toISOString(),
        by: memberLabel(),
        status: "open",
        workflow: defaultWorkflow(),
        entities: [],
        hr_edges: [],
      });
    }
    const clean = String(label || "").trim();
    if (!clean) return null;
    let ent = findEntityByKey(type, clean, uid);
    if (ent) {
      if (uid && !ent.uid) ent.uid = uid;
      if (note) ent.note = note;
      if (seed) ent.seed = true;
    } else {
      ent = {
        id: `${type}:${normUid(uid) || normName(clean)}:${Date.now().toString(36)}`,
        type,
        label: clean,
        uid: uid || "",
        seed: !!seed,
        note: note || "",
        accounts: [],
        manual: true,
      };
      draft.entities.push(ent);
    }
    if (linkFromId) addEdge(linkFromId, ent.id);
    else if (!ent.seed) {
      const seedEnt = draft.entities.find((e) => e.seed);
      if (seedEnt) addEdge(seedEnt.id, ent.id);
    }
    selectedEntityId = ent.id;
    return ent;
  }

  function removeEntity(id) {
    if (!draft) return;
    const ent = draft.entities.find((e) => e.id === id);
    if (!ent || ent.seed) {
      msg("Seed-Firma bleibt im Fallfokus.");
      return;
    }
    draft.entities = draft.entities.filter((e) => e.id !== id);
    draft.hr_edges = (draft.hr_edges || []).filter((e) => e.from !== id && e.to !== id);
    if (selectedEntityId === id) selectedEntityId = draft.entities[0]?.id || null;
    msg(`Entfernt: ${d(ent.label, ent.type === "person" ? "person" : "company")}`);
    renderEntityList();
    renderEntityDetail();
    updateCockpitCards();
  }

  function renderEntityList() {
    const list = document.getElementById("ppEntityList");
    if (!list || !draft) return;
    list.innerHTML = (draft.entities || [])
      .filter((e) => e.type === "person" || e.type === "company")
      .map((e) => {
        const kind = e.type === "person" ? "person" : "company";
        return `<li>
          <button type="button" class="pp-entity-btn${e.id === selectedEntityId ? " is-active" : ""}" data-id="${esc(e.id)}">
            <span class="profiler-entity-type">${e.type === "person" ? "Person" : "Firma"}</span>
            <strong>${esc(d(e.label, kind))}</strong>
            ${e.seed ? `<span class="profiler-seed-tag">Seed</span>` : ""}
            ${e.manual ? `<span class="profiler-manual-tag">Manuell</span>` : ""}
          </button>
        </li>`;
      })
      .join("");
    list.querySelectorAll(".pp-entity-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedEntityId = btn.dataset.id;
        renderEntityList();
        renderEntityDetail();
      });
    });
  }

  function selectedEntity() {
    return draft?.entities?.find((e) => e.id === selectedEntityId) || null;
  }

  function renderEntityDetail() {
    const box = document.getElementById("ppEntityDetail");
    const ent = selectedEntity();
    if (!box) return;
    if (!ent) {
      box.innerHTML = `<p class="fraud-help">Links einen Knoten wählen.</p>`;
      return;
    }
    const kind = ent.type === "person" ? "person" : "company";
    box.innerHTML = `
      <div class="pp-detail-head">
        <span class="profiler-entity-type">${ent.type === "person" ? "Person" : "Firma"}</span>
        <h3>${esc(d(ent.label, kind))}</h3>
        ${ent.uid ? `<p class="profiler-entity-uid">${esc(d(ent.uid, "uid"))}</p>` : ""}
        ${ent.note ? `<p class="profiler-rel-note">${esc(ent.note)}</p>` : ""}
      </div>
      <div class="pp-detail-actions">
        ${
          ent.type === "person"
            ? `<button type="button" class="btn-check" id="ppScanBtn">Scannen</button>`
            : `<button type="button" class="btn-check" id="ppOrganeBtn">Organe laden</button>`
        }
        ${
          ent.seed
            ? ""
            : `<button type="button" class="btn-nav" id="ppRemoveBtn">Streichen</button>`
        }
      </div>
      <p class="fraud-help">${
        ent.type === "person"
          ? "SHAB-Suche nach weiteren Firmen dieser Person."
          : "Personen und verbundene Firmen aus dem HR nachladen."
      }</p>`;
    document.getElementById("ppScanBtn")?.addEventListener("click", () => scanPerson(ent.id));
    document.getElementById("ppOrganeBtn")?.addEventListener("click", () => expandOrgans(ent.id));
    document.getElementById("ppRemoveBtn")?.addEventListener("click", () => removeEntity(ent.id));
  }

  let scanAbort = null;
  let scanProgressTimer = null;

  function openScanModal(personLabel) {
    const modal = document.getElementById("ppScanModal");
    const bar = document.getElementById("ppScanProgressBar");
    document.getElementById("ppScanModalPerson").textContent = personLabel || "";
    document.getElementById("ppScanModalStatus").textContent =
      "Weitere Firmen im kantonalen SHAB (wie Beziehungsnetzwerk, ~12 Jahre)…";
    if (bar) bar.style.width = "8%";
    modal?.classList.remove("hidden");
    document.body.classList.add("pp-scan-blocking");
    clearInterval(scanProgressTimer);
    let w = 8;
    scanProgressTimer = setInterval(() => {
      w = Math.min(92, w + Math.random() * 4 + 1.5);
      if (bar) bar.style.width = `${w}%`;
    }, 700);
  }

  function closeScanModal() {
    clearInterval(scanProgressTimer);
    scanProgressTimer = null;
    document.getElementById("ppScanModal")?.classList.add("hidden");
    document.body.classList.remove("pp-scan-blocking");
    const bar = document.getElementById("ppScanProgressBar");
    if (bar) bar.style.width = "0%";
  }

  function cancelScan() {
    if (scanAbort) {
      scanAbort.abort();
      scanAbort = null;
    }
    closeScanModal();
    msg("Scan abgebrochen.");
  }

  async function scanPerson(entityId) {
    const ent = draft?.entities?.find((e) => e.id === entityId);
    if (!ent || ent.type !== "person") return;
    const panel = document.getElementById("ppScanPanel");
    const status = document.getElementById("ppScanStatus");
    const list = document.getElementById("ppScanList");
    const addBtn = document.getElementById("ppScanAddSelected");
    scanSourceId = entityId;
    scanMatches = [];
    if (list) list.innerHTML = "";
    addBtn?.classList.add("hidden");
    panel?.classList.add("hidden");

    if (scanAbort) scanAbort.abort();
    scanAbort = new AbortController();
    openScanModal(d(ent.label, "person"));

    try {
      // Same scope as Beziehungsnetzwerk L3: cantonal SHAB via seed registry
      const qs = new URLSearchParams({ name: ent.label });
      if (draft.seed_uid) qs.set("exclude_uid", draft.seed_uid);
      const regId = draft.seed_registry_office_id || seedCompany?.registry_office_id;
      const canton = cantonCode(draft.seed_canton || seedCompany?.canton || "");
      if (regId) qs.set("registry_office_id", String(regId));
      if (canton) qs.set("canton", canton);
      const resp = await fetch(`/api/hr-network/person-search?${qs}`, {
        signal: scanAbort.signal,
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      scanMatches = data.matches || [];
      const already = new Set(
        (draft.entities || [])
          .filter((e) => e.type === "company")
          .map((e) => normUid(e.uid) || normName(e.label))
      );
      closeScanModal();
      panel?.classList.remove("hidden");
      document.getElementById("ppScanTitle").textContent = `Scan — ${d(ent.label, "person")}`;
      const elapsed = data.elapsed_seconds != null ? ` · ${data.elapsed_seconds}s` : "";
      const scope = data.registry_scope ? ` · ${data.registry_scope}` : "";
      if (status) {
        if (scanMatches.length) {
          status.textContent = `${scanMatches.length} Treffer${data.search_complete === false ? " (teilweise)" : ""}${scope}${elapsed}`;
        } else if (data.search_complete === false) {
          status.textContent = `Zeitlimit — keine Treffer in ${data.scanned_months || "?"}/${data.total_months || "?"} Monaten${scope}${elapsed}. Erneut versuchen oder Deep-Scan.`;
        } else {
          status.textContent = `Keine weiteren Firmen gefunden${scope}${elapsed}`;
        }
      }
      list.innerHTML = scanMatches
        .map((m, i) => {
          const key = normUid(m.uid) || normName(m.name);
          const inSnip = already.has(key);
          return `<li class="profiler-scan-item">
            <label>
              <input type="checkbox" data-scan-idx="${i}" ${inSnip ? "disabled" : "checked"}>
              <span>
                <strong>${esc(d(m.name || "—", "company"))}</strong>
                ${m.uid ? `<span class="profiler-entity-uid">${esc(d(m.uid, "uid"))}</span>` : ""}
                ${inSnip ? `<span class="profiler-manual-tag">im Snip</span>` : ""}
              </span>
            </label>
          </li>`;
        })
        .join("");
      if (scanMatches.some((_, i) => !list.querySelector(`[data-scan-idx="${i}"]`)?.disabled)) {
        addBtn?.classList.remove("hidden");
      }
    } catch (err) {
      closeScanModal();
      if (err?.name === "AbortError") return;
      panel?.classList.remove("hidden");
      document.getElementById("ppScanTitle").textContent = `Scan — ${d(ent.label, "person")}`;
      if (status) status.textContent = err.message || "Scan fehlgeschlagen";
    } finally {
      scanAbort = null;
    }
  }

  function addSelectedScan() {
    const list = document.getElementById("ppScanList");
    if (!list || !scanSourceId) return;
    let n = 0;
    list.querySelectorAll("input[data-scan-idx]:checked:not(:disabled)").forEach((input) => {
      const m = scanMatches[Number(input.dataset.scanIdx)];
      if (!m?.name) return;
      upsertEntity({
        type: "company",
        label: m.name,
        uid: m.uid || "",
        note: "Person-Scan",
        linkFromId: scanSourceId,
      });
      n += 1;
    });
    if (n) {
      ensureWorkflow(draft).workflow.network_expanded = true;
      msg(`${n} Firma/Firmen übernommen`);
      renderEntityList();
      renderEntityDetail();
      updateCockpitCards();
    }
  }

  async function expandOrgans(entityId) {
    const ent = draft?.entities?.find((e) => e.id === entityId);
    if (!ent || ent.type !== "company") return;
    msg(`Lade Organe für ${d(ent.label, "company")}…`);
    try {
      const data = await loadHrNetwork(ent.label, ent.uid);
      let added = 0;
      for (const n of data.nodes || []) {
        const type = n.type === "person" || n.node_type === "person" ? "person" : "company";
        const label = String(n.name || n.label || "").split("\n")[0].trim();
        if (!label) continue;
        if (type === "company" && (normUid(n.uid) === normUid(ent.uid) || normName(label) === normName(ent.label))) {
          continue;
        }
        const before = draft.entities.length;
        upsertEntity({
          type,
          label,
          uid: n.uid || "",
          note: type === "person" ? "Organ" : "Verbunden",
          linkFromId: ent.id,
        });
        if (draft.entities.length > before) added += 1;
      }
      ensureWorkflow(draft).workflow.network_expanded = true;
      msg(`Organe: ${added} neue Knoten`);
      renderEntityList();
      renderEntityDetail();
      updateCockpitCards();
    } catch (err) {
      msg(err.message || "Organe laden fehlgeschlagen");
    }
  }

  function wireAdd() {
    const typeEl = document.getElementById("ppAddType");
    const nameEl = document.getElementById("ppAddName");
    const noteEl = document.getElementById("ppAddNote");
    const suggest = document.getElementById("ppAddSuggest");

    document.getElementById("ppShowAdd")?.addEventListener("click", () => {
      document.getElementById("ppAddBox")?.classList.toggle("hidden");
    });

    nameEl?.addEventListener("input", () => {
      pendingCompanyPick = null;
      const q = nameEl.value.trim();
      clearTimeout(addSuggestTimer);
      if (typeEl?.value !== "company" || q.length < 2) {
        suggest?.classList.add("hidden");
        return;
      }
      addSuggestTimer = setTimeout(async () => {
        try {
          const resp = await fetch(`/api/hr-network/search?q=${encodeURIComponent(q)}&limit=8`);
          const data = await resp.json();
          const results = data.results || [];
          if (!suggest || !results.length) {
            suggest?.classList.add("hidden");
            return;
          }
          suggest.classList.remove("hidden");
          suggest.innerHTML = results
            .map(
              (r) => `<li data-name="${esc(r.name || "")}" data-uid="${esc(r.uid || "")}">
                <strong>${esc(d(r.name || "", "company"))}</strong>
                ${r.uid ? `<span>${esc(d(r.uid, "uid"))}</span>` : ""}
              </li>`
            )
            .join("");
          suggest.querySelectorAll("li").forEach((li) => {
            li.addEventListener("click", () => {
              nameEl.value = li.dataset.name || "";
              pendingCompanyPick = { name: li.dataset.name, uid: li.dataset.uid };
              suggest.classList.add("hidden");
            });
          });
        } catch (_) {
          suggest?.classList.add("hidden");
        }
      }, 280);
    });

    document.getElementById("ppAddBtn")?.addEventListener("click", () => {
      const type = typeEl?.value === "company" ? "company" : "person";
      const name = (pendingCompanyPick?.name || nameEl?.value || "").trim();
      const uid = pendingCompanyPick?.uid || "";
      const note = (noteEl?.value || "").trim();
      if (!name) {
        msg("Name eingeben.");
        return;
      }
      upsertEntity({
        type,
        label: name,
        uid,
        note: note || (type === "person" ? "Kenntnisstand" : ""),
      });
      if (type === "company") ensureWorkflow(draft).workflow.network_expanded = true;
      if (nameEl) nameEl.value = "";
      if (noteEl) noteEl.value = "";
      pendingCompanyPick = null;
      suggest?.classList.add("hidden");
      document.getElementById("ppAddBox")?.classList.add("hidden");
      renderEntityList();
      renderEntityDetail();
      updateCockpitCards();
      msg("Knoten übernommen");
    });
  }

  /* ── Signale ── */

  function renderWorkflow() {
    const list = document.getElementById("ppWorkflowList");
    if (!list || !draft) return;
    ensureWorkflow(draft);
    const wf = draft.workflow;
    list.innerHTML = WORKFLOW_FIELDS.map((f) => {
      const checked = !!wf[f.key];
      return `<li class="profiler-wf-item${checked ? " is-done" : ""}">
        <label class="profiler-wf-check">
          <input type="checkbox" data-wf-key="${f.key}" ${checked ? "checked" : ""}>
          <span>
            <strong>${esc(f.title)}</strong>
            <span class="profiler-wf-help">${esc(f.help)}</span>
          </span>
        </label>
        ${
          f.date
            ? `<label class="profiler-field profiler-wf-date">
                <span>Seit</span>
                <input type="date" data-wf-aml-date value="${esc(wf.aml_since || "")}">
              </label>`
            : ""
        }
        ${
          f.action
            ? `<a class="ca-tool-link" href="${esc(f.action.href)}" target="_blank" rel="noopener">${esc(f.action.label)} ↗</a>`
            : ""
        }
      </li>`;
    }).join("");
    list.querySelectorAll("[data-wf-key]").forEach((input) => {
      input.addEventListener("change", () => {
        wf[input.dataset.wfKey] = input.checked;
        input.closest(".profiler-wf-item")?.classList.toggle("is-done", input.checked);
        updateCockpitCards();
      });
    });
    list.querySelector("[data-wf-aml-date]")?.addEventListener("change", (e) => {
      wf.aml_since = e.target.value || "";
      if (wf.aml_since) {
        wf.aml_freeze = true;
        const cb = list.querySelector('[data-wf-key="aml_freeze"]');
        if (cb) cb.checked = true;
        list.querySelector('[data-wf-key="aml_freeze"]')?.closest(".profiler-wf-item")?.classList.add("is-done");
        updateCockpitCards();
      }
    });
  }

  /* ── Konten ── */

  function normalizeClearing(raw) {
    let s = String(raw || "").replace(/\s+/g, "").toUpperCase();
    const iban = s.replace(/[^A-Z0-9]/g, "");
    if (iban.startsWith("CH") && iban.length >= 9) s = iban.slice(4, 9);
    return s.replace(/\D/g, "").slice(0, 6);
  }

  async function lookupBank(raw) {
    const q = normalizeClearing(raw);
    if (!q) return null;
    if (bankLookupCache.has(q)) return bankLookupCache.get(q);
    try {
      const resp = await fetch(`/api/swiss-banks/lookup?q=${encodeURIComponent(q)}`);
      const data = await resp.json();
      bankLookupCache.set(q, data.match || null);
      return data.match || null;
    } catch (_) {
      return null;
    }
  }

  function bankDisplay(match, clearing) {
    if (!match) return clearing ? `Clearing ${clearing}` : "";
    const iid = match.sic_iid || String(match.iid || "").padStart(5, "0");
    return `${match.name}${match.town ? ` · ${match.town}` : ""} (${iid})`;
  }

  function renderAccEntityList() {
    const list = document.getElementById("ppAccEntityList");
    if (!list || !draft) return;
    list.innerHTML = (draft.entities || [])
      .filter((e) => e.type === "person" || e.type === "company")
      .map((e) => {
        const n = (e.accounts || []).length;
        const kind = e.type === "person" ? "person" : "company";
        return `<li>
          <button type="button" class="pp-entity-btn${e.id === selectedEntityId ? " is-active" : ""}" data-id="${esc(e.id)}">
            <span class="profiler-entity-type">${e.type === "person" ? "Person" : "Firma"}</span>
            <strong>${esc(d(e.label, kind))}</strong>
            <span class="profiler-acc-count">${n}</span>
          </button>
        </li>`;
      })
      .join("");
    list.querySelectorAll(".pp-entity-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedEntityId = btn.dataset.id;
        renderAccEntityList();
        renderAccEditor();
      });
    });
  }

  function renderAccEditor() {
    const ent = selectedEntity();
    const empty = document.getElementById("ppAccEmpty");
    const body = document.getElementById("ppAccBody");
    const title = document.getElementById("ppAccTitle");
    const list = document.getElementById("ppAccList");
    if (!ent) {
      empty?.classList.remove("hidden");
      body?.classList.add("hidden");
      return;
    }
    empty?.classList.add("hidden");
    body?.classList.remove("hidden");
    if (title) {
      title.textContent = `Konten — ${d(ent.label, ent.type === "person" ? "person" : "company")}`;
    }
    list.innerHTML = (ent.accounts || [])
      .map(
        (a, idx) => `
      <li class="profiler-account">
        <label class="profiler-field">
          <span>Clearing / IBAN</span>
          <input type="text" class="pp-acc-clearing" data-acc-idx="${idx}" value="${esc(d(a.clearing || "", "clearing"))}" data-raw="${esc(a.clearing || "")}" placeholder="00700 oder CH…">
        </label>
        <span class="profiler-bank-label" data-bank-acc="${idx}">${esc(d(a.bank_label || "—", "bank"))}</span>
        <label class="profiler-field profiler-field-note">
          <span>Notiz</span>
          <input type="text" class="pp-acc-note" data-acc-idx="${idx}" value="${esc(a.note || "")}">
        </label>
        <button type="button" class="ca-tool-link pp-acc-del" data-acc-idx="${idx}">Entfernen</button>
      </li>`
      )
      .join("");

    // Show raw clearing in input when not anonymizing for editing
    list.querySelectorAll(".pp-acc-clearing").forEach((input) => {
      if (typeof isAnonymizeMode === "function" && !isAnonymizeMode()) {
        input.value = input.dataset.raw || "";
      }
      input.addEventListener("change", async () => {
        const i = Number(input.dataset.accIdx);
        const clearing = normalizeClearing(input.value);
        ent.accounts[i].clearing = clearing;
        input.dataset.raw = clearing;
        input.value =
          typeof isAnonymizeMode === "function" && isAnonymizeMode()
            ? d(clearing, "clearing")
            : clearing;
        const match = await lookupBank(clearing);
        ent.accounts[i].bank_label = bankDisplay(match, clearing);
        ent.accounts[i].bic = match?.bic || "";
        const lab = list.querySelector(`[data-bank-acc="${i}"]`);
        if (lab) lab.textContent = d(ent.accounts[i].bank_label || "—", "bank");
        updateCockpitCards();
      });
    });
    list.querySelectorAll(".pp-acc-note").forEach((input) => {
      input.addEventListener("input", () => {
        ent.accounts[Number(input.dataset.accIdx)].note = input.value;
      });
    });
    list.querySelectorAll(".pp-acc-del").forEach((btn) => {
      btn.addEventListener("click", () => {
        ent.accounts.splice(Number(btn.dataset.accIdx), 1);
        renderAccEditor();
        renderAccEntityList();
        updateCockpitCards();
      });
    });
  }

  function addAccount() {
    const ent = selectedEntity();
    if (!ent) return;
    ent.accounts = ent.accounts || [];
    ent.accounts.push({
      id: `acc-${Date.now()}`,
      clearing: "",
      bank_label: "",
      bic: "",
      note: "",
    });
    renderAccEditor();
    renderAccEntityList();
  }

  /* ── Export ── */

  function namesPayload() {
    const companies = [];
    const persons = [];
    for (const e of draft?.entities || []) {
      if (e.type === "person") persons.push(d(e.label, "person"));
      else if (e.type === "company") companies.push(d(e.label, "company"));
    }
    return {
      seed_name: d(draft?.seed_name || "", "company"),
      seed_uid: d(draft?.seed_uid || "", "uid"),
      companies,
      persons,
    };
  }

  function renderExport() {
    const p = namesPayload();
    const pre = document.getElementById("ppNamePreview");
    if (pre) {
      pre.textContent = [
        `Fallfokus: ${p.seed_name}${p.seed_uid ? ` (${p.seed_uid})` : ""}`,
        "",
        "FIRMEN / VEREINE",
        ...p.companies,
        "",
        "PERSONEN",
        ...p.persons,
      ].join("\n");
    }
  }

  function copyNames() {
    const text = document.getElementById("ppNamePreview")?.textContent || "";
    navigator.clipboard?.writeText(text).then(
      () => msg("Namen kopiert — bereit für Kernbank."),
      () => msg("Clipboard fehlgeschlagen")
    );
  }

  async function downloadPdf() {
    if (!draft?.entities?.length) return;
    msg("PDF wird erstellt…");
    try {
      const resp = await fetch("/api/profiler/screening-pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(namesPayload()),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `profiler-screening.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      msg("PDF heruntergeladen.");
    } catch (err) {
      msg(err.message || "PDF fehlgeschlagen");
    }
  }

  function destroyGraph() {
    try {
      profilerNetwork?.destroy();
    } catch (_) {}
    profilerNetwork = null;
    const el = document.getElementById("ppGraph");
    if (el) el.innerHTML = "";
  }

  function graphNodeStyle(e) {
    if (e.type === "external") {
      return {
        label: d(e.label, "bank"),
        shape: "hexagon",
        color: { background: "#3b1f0a", border: "#fb923c" },
        font: { color: "#ffedd5", size: 11 },
        borderWidth: 2,
      };
    }
    if (e.type === "account") {
      const bank = d((e.bank_label || e.label || "").split(" (")[0], "bank");
      const cl = d(e.clearing || "", "clearing");
      return {
        label: cl ? `${bank}\n${cl}` : bank,
        shape: "diamond",
        color: { background: "#14532d", border: "#4ade80" },
        font: { color: "#dcfce7", size: 11, multi: true },
        borderWidth: 2,
      };
    }
    const isPerson = e.type === "person";
    return {
      label: d(e.label, isPerson ? "person" : "company"),
      shape: isPerson ? "dot" : "box",
      color: e.seed
        ? { background: "#7f1d1d", border: "#f87171" }
        : isPerson
          ? { background: "#1e293b", border: "#94a3b8" }
          : { background: "#1e3a5f", border: "#38bdf8" },
      font: { color: "#f1f5f9", size: 12 },
      borderWidth: e.seed ? 2 : 1,
      size: isPerson ? 18 : undefined,
    };
  }

  function renderGraph() {
    const wrap = document.getElementById("ppGraphWrap");
    const container = document.getElementById("ppGraph");
    if (!container || typeof vis === "undefined" || !draft?.entities?.length) return;
    wrap?.classList.remove("hidden");
    destroyGraph();
    const visNodes = [];
    const visEdges = [];
    const bankIds = new Map();
    const accountNodeKeys = new Set(
      (draft.entities || [])
        .filter((e) => e.type === "account" && e.owner_id && e.clearing)
        .map((e) => `${e.owner_id}:${normalizeClearing(e.clearing)}`)
    );
    for (const e of draft.entities) {
      visNodes.push({ id: e.id, ...graphNodeStyle(e) });
    }
    for (const e of draft.hr_edges || []) {
      const kind = e.kind || "hr";
      if (kind === "payment") {
        const cap = formatPaymentCaption(e);
        visEdges.push({
          from: e.from,
          to: e.to,
          label: cap,
          arrows: "to",
          color: { color: "#fbbf24" },
          font: { color: "#fde68a", size: 10, multi: true, strokeWidth: 0 },
          width: 3,
          smooth: { type: "curvedCW", roundness: 0.25 },
        });
      } else if (kind === "relation" || kind === "other") {
        visEdges.push({
          from: e.from,
          to: e.to,
          label: e.note || e.label || (kind === "relation" ? "Bezug" : "Link"),
          arrows: "to",
          color: { color: "#c084fc" },
          font: { color: "#e9d5ff", size: 10, strokeWidth: 0 },
          width: 2,
          dashes: [6, 4],
        });
      } else {
        visEdges.push({
          from: e.from,
          to: e.to,
          arrows: "to",
          color: { color: "#64748b" },
          width: 1,
        });
      }
    }
    for (const e of draft.entities) {
      if (e.type === "external" || e.type === "account") continue;
      for (const acc of e.accounts || []) {
        const clearing = normalizeClearing(acc.clearing);
        if (!clearing) continue;
        // Prefer explicit account node when used in Zahlungsstrom
        if (accountNodeKeys.has(`${e.id}:${clearing}`)) continue;
        const bankId = `bank:${clearing}`;
        if (!bankIds.has(clearing)) {
          bankIds.set(clearing, bankId);
          visNodes.push({
            id: bankId,
            label: `${d(acc.bank_label || clearing, "bank").split(" (")[0]}\n${d(clearing, "clearing")}`,
            shape: "diamond",
            color: { background: "#134e4a", border: "#2dd4bf" },
            font: { color: "#ccfbf1", size: 11, multi: true },
          });
        }
        visEdges.push({
          from: e.id,
          to: bankId,
          label: "Konto",
          arrows: "to",
          color: { color: "#2dd4bf" },
          dashes: true,
          width: 2,
        });
      }
    }
    profilerNetwork = new vis.Network(
      container,
      { nodes: new vis.DataSet(visNodes), edges: new vis.DataSet(visEdges) },
      {
        interaction: { hover: true, navigationButtons: true, keyboard: false },
        physics: {
          barnesHut: { gravitationalConstant: -3200, springLength: 140 },
          stabilization: { iterations: 80 },
        },
      }
    );
  }

  const LINK_KIND_LABEL = {
    payment: "Zahlungsstrom",
    relation: "Beziehung",
    other: "Sonstiges",
    hr: "HR",
  };

  function listSnipAccounts() {
    const out = [];
    for (const e of draft?.entities || []) {
      if (e.type === "external" || e.type === "account") continue;
      for (const a of e.accounts || []) {
        const clearing = normalizeClearing(a.clearing);
        if (!clearing) continue;
        out.push({
          id: `acct:${e.id}:${clearing}`,
          ownerId: e.id,
          ownerLabel: e.label,
          ownerType: e.type,
          clearing,
          bank_label: a.bank_label || `Clearing ${clearing}`,
        });
      }
    }
    return out;
  }

  function ensureFlowNode({ id, type, label, uid, clearing, bank_label, owner_id }) {
    if (!draft) return null;
    let ent = draft.entities.find((e) => e.id === id);
    if (ent) {
      if (label) ent.label = label;
      return ent;
    }
    ent = {
      id,
      type,
      label,
      uid: uid || "",
      clearing: clearing || "",
      bank_label: bank_label || "",
      owner_id: owner_id || "",
      seed: false,
      manual: true,
      accounts: [],
    };
    draft.entities.push(ent);
    return ent;
  }

  function resolveEndpoint(side) {
    const type = document.getElementById(side === "from" ? "ppFromType" : "ppToType")?.value;
    if (type === "external") {
      const raw = document.getElementById(side === "from" ? "ppFromExternal" : "ppToExternal")?.value?.trim();
      if (!raw) return { error: "Externe Bank / Institut angeben." };
      const id = `ext:${normName(raw).replace(/\s+/g, "-") || "extern"}`;
      ensureFlowNode({ id, type: "external", label: raw });
      return { id, label: raw };
    }
    if (type === "account") {
      const id = document.getElementById(side === "from" ? "ppFromAccount" : "ppToAccount")?.value;
      const acc = listSnipAccounts().find((a) => a.id === id);
      if (!acc) return { error: "Konto im Snip wählen (zuerst unter Konten Clearing setzen)." };
      const label = `${acc.bank_label.split(" (")[0]} · ${acc.ownerLabel}`;
      ensureFlowNode({
        id: acc.id,
        type: "account",
        label,
        clearing: acc.clearing,
        bank_label: acc.bank_label,
        owner_id: acc.ownerId,
      });
      // keep owner→account edge visual via graph bank link; also soft link owner
      addEdge(acc.ownerId, acc.id, { kind: "hr", label: "Konto" });
      return { id: acc.id, label };
    }
    const id = document.getElementById(side === "from" ? "ppFromEntity" : "ppToEntity")?.value;
    const ent = draft?.entities?.find((e) => e.id === id);
    if (!ent) return { error: "Person / Firma wählen." };
    return { id, label: ent.label };
  }

  function syncEndpointVisibility(side) {
    const type = document.getElementById(side === "from" ? "ppFromType" : "ppToType")?.value;
    const ext = document.getElementById(side === "from" ? "ppFromExternal" : "ppToExternal");
    const acc = document.getElementById(side === "from" ? "ppFromAccount" : "ppToAccount");
    const ent = document.getElementById(side === "from" ? "ppFromEntity" : "ppToEntity");
    ext?.classList.toggle("hidden", type !== "external");
    acc?.classList.toggle("hidden", type !== "account");
    ent?.classList.toggle("hidden", type !== "entity");
  }

  function accountOptionsHtml() {
    const accs = listSnipAccounts();
    if (!accs.length) {
      return `<option value="">— Keine Konten (zuerst Konten pflegen) —</option>`;
    }
    return accs
      .map((a) => {
        const owner = d(a.ownerLabel, a.ownerType === "person" ? "person" : "company");
        const bank = d(a.bank_label.split(" (")[0], "bank");
        const cl = d(a.clearing, "clearing");
        return `<option value="${esc(a.id)}">${esc(`${bank} (${cl}) · ${owner}`)}</option>`;
      })
      .join("");
  }

  function entityOptionsHtml(selectedId) {
    return (draft?.entities || [])
      .filter((e) => e.type === "person" || e.type === "company")
      .map((e) => {
        const kind = e.type === "person" ? "person" : "company";
        return `<option value="${esc(e.id)}" ${e.id === selectedId ? "selected" : ""}>${esc(d(e.label, kind))}</option>`;
      })
      .join("");
  }

  function endpointLabel(id) {
    const e = draft?.entities?.find((x) => x.id === id);
    if (!e) return id;
    if (e.type === "external") return d(e.label, "bank");
    if (e.type === "account") {
      const owner = draft?.entities?.find((x) => x.id === e.owner_id);
      const bank = d((e.bank_label || e.label || "").split(" (")[0], "bank");
      const ownerL = owner
        ? d(owner.label, owner.type === "person" ? "person" : "company")
        : d(e.clearing, "clearing");
      return `${bank} · ${ownerL}`;
    }
    return d(e.label, e.type === "person" ? "person" : "company");
  }

  function formatPaymentCaption(e) {
    const parts = [];
    if (e.amount != null && e.amount !== "") {
      parts.push(`${e.currency || "CHF"} ${e.amount}`);
    }
    if (e.note) parts.push(e.note);
    return parts.join(" · ") || e.label || "Zahlung";
  }

  function renderLinkEditor() {
    const fromAcc = document.getElementById("ppFromAccount");
    const toAcc = document.getElementById("ppToAccount");
    const fromEnt = document.getElementById("ppFromEntity");
    const toEnt = document.getElementById("ppToEntity");
    if (fromAcc) fromAcc.innerHTML = accountOptionsHtml();
    if (toAcc) toAcc.innerHTML = accountOptionsHtml();
    if (fromEnt) fromEnt.innerHTML = entityOptionsHtml(selectedEntityId);
    if (toEnt) {
      const seed = draft?.entities?.find((e) => e.seed);
      toEnt.innerHTML = entityOptionsHtml(seed?.id);
    }
    syncEndpointVisibility("from");
    syncEndpointVisibility("to");
    const kind = document.getElementById("ppLinkKind")?.value || "payment";
    document.getElementById("ppAmountRow")?.classList.toggle("hidden", kind !== "payment");

    const list = document.getElementById("ppLinkList");
    if (!list) return;
    const manual = (draft?.hr_edges || []).filter((e) => (e.kind || "hr") !== "hr");
    if (!manual.length) {
      list.innerHTML = `<li class="fraud-help">Noch keine Zahlungsströme / Extra-Links.</li>`;
      return;
    }
    list.innerHTML = manual
      .map((e) => {
        const cap = e.kind === "payment" ? formatPaymentCaption(e) : e.note || e.label || "";
        return `<li class="pp-link-item">
          <div>
            <strong>${esc(LINK_KIND_LABEL[e.kind] || e.kind)}</strong>
            <span>${esc(endpointLabel(e.from))} → ${esc(endpointLabel(e.to))}</span>
            ${cap ? `<span class="fraud-help">${esc(cap)}</span>` : ""}
          </div>
          <button type="button" class="ca-tool-link" data-del-link="${esc(e.id)}">✕</button>
        </li>`;
      })
      .join("");
    list.querySelectorAll("[data-del-link]").forEach((btn) => {
      btn.addEventListener("click", () => {
        draft.hr_edges = (draft.hr_edges || []).filter((e) => e.id !== btn.dataset.delLink);
        // prune orphan external/account nodes not referenced
        pruneOrphanFlowNodes();
        renderLinkEditor();
        renderGraph();
        updateCockpitCards();
        msg("Verbindung entfernt.");
      });
    });
  }

  function pruneOrphanFlowNodes() {
    if (!draft) return;
    const used = new Set();
    for (const e of draft.hr_edges || []) {
      used.add(e.from);
      used.add(e.to);
    }
    draft.entities = draft.entities.filter((e) => {
      if (e.type !== "external" && e.type !== "account") return true;
      return used.has(e.id);
    });
  }

  function addManualLink() {
    const kind = document.getElementById("ppLinkKind")?.value || "payment";
    const from = resolveEndpoint("from");
    if (from.error) {
      msg(from.error);
      return;
    }
    const to = resolveEndpoint("to");
    if (to.error) {
      msg(to.error);
      return;
    }
    if (from.id === to.id) {
      msg("Von und Nach müssen verschieden sein.");
      return;
    }
    const amountRaw = document.getElementById("ppLinkAmount")?.value?.trim() || "";
    const currency = document.getElementById("ppLinkCurrency")?.value || "CHF";
    const note = document.getElementById("ppLinkNote")?.value?.trim() || "";
    const exists = (draft.hr_edges || []).some(
      (e) => e.from === from.id && e.to === to.id && (e.kind || "hr") === kind && (e.note || "") === note
    );
    if (exists) {
      msg("Diese Verbindung existiert bereits.");
      return;
    }
    draft.hr_edges = draft.hr_edges || [];
    draft.hr_edges.push({
      id: `edge-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      from: from.id,
      to: to.id,
      kind,
      label: LINK_KIND_LABEL[kind] || kind,
      note,
      amount: amountRaw || null,
      currency: amountRaw ? currency : null,
    });
    const amountEl = document.getElementById("ppLinkAmount");
    const noteEl = document.getElementById("ppLinkNote");
    if (amountEl) amountEl.value = "";
    if (noteEl) noteEl.value = "";
    renderLinkEditor();
    renderGraph();
    updateCockpitCards();
    msg("Zahlungsstrom / Verbindung erfasst — Fall speichern.");
  }

  function wireEndpointControls() {
    ["ppFromType", "ppToType"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", () => {
        syncEndpointVisibility("from");
        syncEndpointVisibility("to");
      });
    });
    document.getElementById("ppLinkKind")?.addEventListener("change", () => {
      const kind = document.getElementById("ppLinkKind")?.value;
      document.getElementById("ppAmountRow")?.classList.toggle("hidden", kind !== "payment");
    });
  }

  function saveDraft() {
    if (!draft?.entities?.length) return;
    ensureWorkflow(draft);
    draft.updated_at = new Date().toISOString();
    draft.by = memberLabel();
    const next = [draft, ...loadSnips().filter((s) => s.id !== draft.id)].slice(0, 40);
    try {
      localStorage.setItem(PROFILER_KEY, JSON.stringify(next));
      msg("Fall gespeichert.");
      updateHeader();
      updateCockpitCards();
    } catch (_) {
      msg("Speichern fehlgeschlagen.");
    }
  }

  async function reloadFromAnalysis() {
    if (!seedCompany?.name && !seedCompany?.uid) {
      msg("Kein Seed — mit ?company= oder ?uid= öffnen.");
      return;
    }
    msg("Lade HR-Netzwerk…");
    try {
      hrGraph = await loadHrNetwork(seedCompany.name, seedCompany.uid);
      if (hrGraph.company) {
        seedCompany = {
          name: hrGraph.company.name || seedCompany.name,
          uid: hrGraph.company.uid || seedCompany.uid,
          canton: cantonCode(hrGraph.company.canton),
          registry_office_id: hrGraph.company.registry_office_id || null,
        };
      }
      const prev = draft && sameSeed(draft, seedCompany) ? draft : null;
      draft = snipFromGraph(hrGraph, seedCompany, prev);
      selectedEntityId = draft.entities.find((e) => e.seed)?.id || draft.entities[0]?.id;
      msg(`${draft.entities.length} Knoten geladen`);
      updateHeader();
      updateCockpitCards();
      if (activeView === "netz") {
        renderEntityList();
        renderEntityDetail();
      }
    } catch (err) {
      msg(err.message || "Laden fehlgeschlagen");
    }
  }

  async function boot() {
    if (!isAdmin()) {
      msg("Nur für Admins.");
      setTimeout(() => {
        location.href = "/";
      }, 600);
      return;
    }

    const snipId = params.get("snip");
    const company = params.get("company") || "";
    const uid = params.get("uid") || "";

    if (snipId) {
      const found = loadSnips().find((s) => s.id === snipId);
      if (found) {
        draft = migrateDraft(JSON.parse(JSON.stringify(found)));
        seedCompany = { name: draft.seed_name, uid: draft.seed_uid };
        selectedEntityId = draft.entities.find((e) => e.seed)?.id || draft.entities[0]?.id;
        msg("Fall geladen.");
      } else {
        msg("Snip nicht gefunden.");
      }
    }

    if (!draft && (company || uid)) {
      seedCompany = { name: company, uid };
      const existing = findOpenSnipForCompany(seedCompany);
      if (existing) {
        draft = migrateDraft(JSON.parse(JSON.stringify(existing)));
        seedCompany = { name: draft.seed_name || company, uid: draft.seed_uid || uid };
        selectedEntityId = draft.entities.find((e) => e.seed)?.id || draft.entities[0]?.id;
        msg("Offener Fall fortgesetzt.");
      } else {
        try {
          msg("Lade Analyse…");
          hrGraph = await loadHrNetwork(company, uid);
          if (hrGraph.company) {
            seedCompany = {
              name: hrGraph.company.name || company,
              uid: hrGraph.company.uid || uid,
              canton: cantonCode(hrGraph.company.canton),
              registry_office_id: hrGraph.company.registry_office_id || null,
            };
          }
          draft = snipFromGraph(hrGraph, seedCompany, null);
          selectedEntityId = draft.entities.find((e) => e.seed)?.id || draft.entities[0]?.id;
          msg(`${draft.entities.length} Knoten — wähle einen Schritt.`);
        } catch (err) {
          draft = ensureWorkflow({
            id: `snip-${Date.now()}`,
            seed_name: company || "Unbekannt",
            seed_uid: uid,
            created_at: new Date().toISOString(),
            by: memberLabel(),
            status: "open",
            workflow: defaultWorkflow(),
            entities: [
              {
                id: `company:${uid || company}`,
                type: "company",
                label: company || "Kernfirma",
                uid,
                seed: true,
                accounts: [],
              },
            ],
            hr_edges: [],
          });
          selectedEntityId = draft.entities[0].id;
          msg(err.message || "HR nicht geladen — Seed manuell gesetzt.");
        }
      }
    }

    if (!draft) {
      msg("Profiler mit Firma öffnen: von der Firmenanalyse «Profiler» oder aus Profiler-Fälle.");
    }

    updateHeader();
    showCockpit();
  }

  function wire() {
    document.querySelectorAll("[data-pp-view]").forEach((btn) => {
      btn.addEventListener("click", () => showView(btn.dataset.ppView));
    });
    document.getElementById("ppBackCockpit")?.addEventListener("click", showCockpit);
    document.getElementById("ppSaveBtn")?.addEventListener("click", saveDraft);
    document.getElementById("ppReloadNetz")?.addEventListener("click", reloadFromAnalysis);
    document.getElementById("ppScanClose")?.addEventListener("click", () => {
      document.getElementById("ppScanPanel")?.classList.add("hidden");
    });
    document.getElementById("ppScanAddSelected")?.addEventListener("click", addSelectedScan);
    document.getElementById("ppScanCancelBtn")?.addEventListener("click", cancelScan);
    document.getElementById("ppScanModalBackdrop")?.addEventListener("click", cancelScan);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !document.getElementById("ppScanModal")?.classList.contains("hidden")) {
        cancelScan();
      }
    });
    document.getElementById("ppAddAccountBtn")?.addEventListener("click", addAccount);
    document.getElementById("ppCopyNamesBtn")?.addEventListener("click", copyNames);
    document.getElementById("ppPdfBtn")?.addEventListener("click", downloadPdf);
    document.getElementById("ppRefreshGraphBtn")?.addEventListener("click", () => {
      renderGraph();
      msg("Karte aktualisiert.");
    });
    document.getElementById("ppLinkAddBtn")?.addEventListener("click", addManualLink);
    wireEndpointControls();
    wireAdd();

    window.onAnonymizeModeChange = function () {
      updateHeader();
      updateCockpitCards();
      if (activeView === "netz") {
        renderEntityList();
        renderEntityDetail();
      }
      if (activeView === "konten") {
        renderAccEntityList();
        renderAccEditor();
      }
      if (activeView === "overview") {
        renderLinkEditor();
        renderGraph();
      }
      if (activeView === "export") renderExport();
      if (typeof renderSiteNav === "function") renderSiteNav();
    };
  }

  const prev = window.onLynxUserReady;
  window.onLynxUserReady = function (u) {
    if (typeof prev === "function") prev(u);
    wire();
    boot();
  };
  if (window.__lynxUser) {
    wire();
    boot();
  }
})();
