/** Unified company analysis — compact workspace + SHAB timeline. */

const LEVEL_META = {
  1: { title: "Firma + Inhaber", speed: "schnell" },
  2: { title: "Ehemalige + Struktur", speed: "schnell" },
  3: { title: "Weitere Firmen", speed: "mittel" },
  4: { title: "Ehemalige vernetzt", speed: "länger" },
  5: { title: "2. Ring", speed: "länger" },
};

const MUTATION_LABELS = {
  "status.neu": "Neueintragung",
  "status.geloescht": "Löschung",
  status: "Statusänderung",
  aenderungorgane: "Organänderung",
  kapitalaenderung: "Kapitaländerung",
  "kapitalaenderung.erhoehung": "Kapitalerhöhung",
  "kapitalaenderung.erniedrigung": "Kapitalherabsetzung",
  fusion: "Fusion",
  vermoegenstransfer: "Vermögenstransfer",
  firmenname: "Namensänderung",
  adresse: "Adressänderung",
  sitzverlegung: "Sitzverlegung",
  zweck: "Zweckänderung",
  statuten: "Statutenänderung",
  rechtsform: "Rechtsformänderung",
  umwandlung: "Umwandlung",
  liquidation: "Liquidation",
};

/** Matches first-load `/api/hr-network` (level 2: Firma + aktuelle/ehemalige). */
let selectedDeepLevel = 2;
let networkInstance = null;
let lastGraph = null;
let currentCompany = null;
let lastAnalysis = null;
let suggestTimer = null;
let currentCaseHit = null;
let branchSignal = null;

const companyInput = document.getElementById("companyInput");
const suggestBox = document.getElementById("suggestBox");
const caError = document.getElementById("caError");
const caStatus = document.getElementById("caStatus");
const caResults = document.getElementById("caResults");

/** UID from autocomplete / recent / deep-link — not typed in the form. */
let pendingUid = "";

const HEAVY_SHAB = 20;
const HEAVY_PERSONS = 12;
const HEAVY_NODES = 40;
const SAFE_MAX_LEVEL = 2;
const SAFE_PERSON_SEARCHES = 4;
const FULL_PERSON_SEARCHES = 8;

let heavyModalResolver = null;

fillLevelSlider(2);
wireSideTabs();
wireSearch();
wireGraphControls();
wireHeavyWarnModal();
document.getElementById("deepBtn")?.addEventListener("click", () => deepAnalyze());
wireRecentSearches();
renderRecentSearches();
setIdleHome(true);
wireIdleHome();

const params = new URLSearchParams(location.search);
if (params.get("tab") === "cases" || params.get("tab") === "list") {
  location.replace("/cases");
}
if (params.get("profiler") === "1" && (params.get("company") || params.get("uid"))) {
  const qs = new URLSearchParams();
  if (params.get("company")) qs.set("company", params.get("company"));
  if (params.get("uid")) qs.set("uid", params.get("uid"));
  location.replace(`/profiler?${qs}`);
}
if (params.get("company") || params.get("uid")) {
  if (params.get("company")) companyInput.value = params.get("company");
  pendingUid = params.get("uid") || "";
  quickAnalyze();
}
loadOpenTeamCases();
loadBranchSignal();

window.onAnonymizeModeChange = function () {
  if (typeof renderSiteNav === "function") renderSiteNav();
  if (lastAnalysis) renderSearchResults(lastAnalysis);
  else if (currentCompany) renderFirmBar(currentCompany, lastAnalysis || {});
};

async function loadBranchSignal() {
  try {
    const resp = await fetch("/api/company-cases/branch-signal");
    if (!resp.ok) return;
    branchSignal = await resp.json();
  } catch (_) { /* optional */ }
}

function purposeMatchesBranch(purpose, branchKey) {
  if (!purpose || !branchKey) return false;
  const p = String(purpose).toLowerCase().replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  return p.includes(branchKey.slice(0, 40)) || branchKey.includes(p.slice(0, 40));
}

function showBranchHintForCompany(company) {
  const el = document.getElementById("branchSignalHint");
  if (!el || !branchSignal?.branches?.length) {
    el?.classList.add("hidden");
    return;
  }
  const purpose = company?.purpose_short || company?.purpose || "";
  const hit = (branchSignal.branches || []).find((b) => purposeMatchesBranch(purpose, b.key));
  if (!hit) {
    el.classList.add("hidden");
    return;
  }
  el.textContent =
    `Branche «${hit.label.slice(0, 80)}» — ${hit.share}% der bestätigten Fälle der letzten ${branchSignal.months} Monate`;
  el.classList.remove("hidden");
}

async function loadOpenTeamCases() {
  const box = document.getElementById("caOpenCases");
  const body = document.getElementById("caOpenCasesBody");
  if (!box || !body) return;
  try {
    const resp = await fetch("/api/company-cases?status=under_review");
    const data = resp.ok ? await resp.json() : { cases: [] };
    const cases = data.cases || [];
    if (!cases.length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    body.innerHTML = `<ul class="fraud-side-list">${cases.slice(0, 8).map((c) => {
      const days = (() => {
        if (!c.opened_at) return 0;
        const t = Date.parse(c.opened_at);
        return Number.isNaN(t) ? 0 : Math.floor((Date.now() - t) / 86400000);
      })();
      const stale = days >= 3
        ? ` <span class="case-stale-badge">Offen seit ${days} Tagen</span>`
        : "";
      return `<li><a href="/cases/${c.id}">
        <strong>${escHtml(c.company_name)}</strong>
        <span class="fraud-entry-meta">in Prüfung · ${escHtml(c.opened_by)} · ${(c.opened_at || "").slice(0, 10)}${stale}</span>
      </a></li>`;
    }).join("")}</ul>`;
  } catch (_) {
    box.classList.add("hidden");
  }
}

function fillLevelSlider(initial) {
  selectedDeepLevel = initial;
  const range = document.getElementById("deepLevelRange");
  const valueEl = document.getElementById("deepLevelValue");
  const titleEl = document.getElementById("deepLevelTitle");
  if (!range) return;

  const sync = () => {
    const level = Number(range.value) || 1;
    selectedDeepLevel = level;
    if (valueEl) valueEl.textContent = String(level);
    if (titleEl) titleEl.textContent = LEVEL_META[level]?.title || "";
    updateHeavyCompanyHint();
  };

  range.value = String(initial);
  range.addEventListener("input", sync);
  sync();
}

/**
 * Fit so the whole network (Firma + Personen + Labels) is visible.
 * Do not re-center on the seed alone — that clips people at the edges.
 */
function fitNetworkView(net, { nodeIds = null, animation = false } = {}) {
  if (!net) return;
  const anim = animation
    ? { animation: typeof animation === "object" ? animation : { duration: 280 } }
    : { animation: false };
  try {
    net.redraw();
    const opts = { ...anim, padding: 88 };
    if (Array.isArray(nodeIds) && nodeIds.length) opts.nodes = nodeIds;
    net.fit(opts);
    // Multi-line person labels sit below nodes; vis fit under-counts them — zoom out a bit.
    const scale = net.getScale();
    if (Number.isFinite(scale) && scale > 0.12) {
      net.moveTo({ scale: Math.max(scale * 0.86, 0.12), ...anim });
    }
  } catch (_) { /* vis may not be ready */ }
}

/** Prefer seed + current persons; fall back to all nodes so nobody is off-canvas. */
function graphFitNodeIds(nodes) {
  const list = nodes || [];
  if (!list.length) return null;
  const important = list.filter((n) => {
    if (n.is_seed || n.type === "company") return true;
    if (n.type === "person" && n.person_status !== "former") return true;
    if (n.case_involved || n.on_watchlist) return true;
    return false;
  });
  // Small graphs / only former people: show everything
  const ids = (important.length >= 2 ? important : list).map((n) => n.id);
  // Always include formers that are connected — full list if few nodes
  if (list.length <= 12) return list.map((n) => n.id);
  return ids;
}

function scheduleNetworkFit(net, nodes) {
  if (!net) return;
  const nodeIds = graphFitNodeIds(nodes);
  const run = (animated) => fitNetworkView(net, { nodeIds, animation: animated });
  const onStable = () => {
    run(false);
    try {
      net.setOptions({ physics: { enabled: false } });
    } catch (_) { /* ignore */ }
  };
  try {
    const handler = () => {
      try { net.off("stabilizationIterationsDone", handler); } catch (_) { /* ignore */ }
      onStable();
    };
    net.on("stabilizationIterationsDone", handler);
  } catch (_) {
    onStable();
  }
  requestAnimationFrame(() => {
    run(false);
    requestAnimationFrame(() => run(false));
  });
  // After idle→results transition / layout settle
  setTimeout(() => run(false), 120);
  setTimeout(() => run(false), 520);
}

function wireGraphControls() {
  document.getElementById("fitGraphBtn")?.addEventListener("click", () => {
    const nodes = lastGraph?.nodes || lastAnalysis?.nodes || [];
    fitNetworkView(networkInstance, {
      nodeIds: graphFitNodeIds(nodes),
      animation: true,
    });
  });
  document.getElementById("graphZoomInBtn")?.addEventListener("click", () => {
    if (!networkInstance) return;
    const scale = networkInstance.getScale();
    networkInstance.moveTo({ scale: Math.min(scale * 1.25, 3.5), animation: { duration: 180 } });
  });
  document.getElementById("graphZoomOutBtn")?.addEventListener("click", () => {
    if (!networkInstance) return;
    const scale = networkInstance.getScale();
    networkInstance.moveTo({ scale: Math.max(scale / 1.25, 0.15), animation: { duration: 180 } });
  });
  document.getElementById("graphFullscreenBtn")?.addEventListener("click", () => {
    toggleGraphFullscreen();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.getElementById("caGraphPanel")?.classList.contains("is-fullscreen")) {
      setGraphFullscreen(false);
    }
  });
  const findInput = document.getElementById("caGraphFindInput");
  findInput?.addEventListener("input", () => {
    clearTimeout(findInput._t);
    findInput._t = setTimeout(() => findGraphNodes(findInput.value), 180);
  });
  findInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      findGraphNodes(findInput.value, { cycle: true });
    }
  });
}

let graphFindHits = [];
let graphFindIndex = 0;

function toggleGraphFullscreen() {
  const panel = document.getElementById("caGraphPanel");
  if (!panel) return;
  setGraphFullscreen(!panel.classList.contains("is-fullscreen"));
}

function setGraphFullscreen(on) {
  const panel = document.getElementById("caGraphPanel");
  const btn = document.getElementById("graphFullscreenBtn");
  if (!panel) return;
  panel.classList.toggle("is-fullscreen", on);
  document.body.classList.toggle("ca-graph-fs-open", on);
  if (btn) {
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "Vollbild beenden (Esc)" : "Vollbild";
    btn.querySelector(".ca-fs-icon-expand")?.classList.toggle("hidden", on);
    btn.querySelector(".ca-fs-icon-collapse")?.classList.toggle("hidden", !on);
  }
  requestAnimationFrame(() => {
    try {
      networkInstance?.redraw();
      const nodes = lastGraph?.nodes || lastAnalysis?.nodes || [];
      fitNetworkView(networkInstance, {
        nodeIds: graphFitNodeIds(nodes),
        animation: true,
      });
    } catch (_) { /* vis may not be ready */ }
    if (on) document.getElementById("caGraphFindInput")?.focus();
  });
}

function findGraphNodes(query, { cycle = false } = {}) {
  const meta = document.getElementById("caGraphFindMeta");
  const q = String(query || "").trim().toLowerCase();
  if (!networkInstance || !q) {
    graphFindHits = [];
    graphFindIndex = 0;
    if (meta) meta.textContent = "";
    try { networkInstance?.unselectAll(); } catch (_) {}
    return;
  }
  const nodes = lastGraph?.nodes || [];
  const hits = nodes.filter((n) => {
    const label = String(n.label || n.name || "").toLowerCase();
    const id = String(n.id || "").toLowerCase();
    return label.includes(q) || id.includes(q);
  }).map((n) => n.id);

  if (!hits.length) {
    graphFindHits = [];
    graphFindIndex = 0;
    if (meta) meta.textContent = "0 Treffer";
    try { networkInstance.unselectAll(); } catch (_) {}
    return;
  }

  if (cycle && graphFindHits.length && graphFindHits.join() === hits.join()) {
    graphFindIndex = (graphFindIndex + 1) % hits.length;
  } else {
    graphFindHits = hits;
    graphFindIndex = 0;
  }

  const id = graphFindHits[graphFindIndex];
  if (meta) {
    meta.textContent = `${graphFindIndex + 1}/${graphFindHits.length}`;
  }
  try {
    networkInstance.selectNodes([id]);
    networkInstance.focus(id, {
      scale: Math.max(networkInstance.getScale(), 1.15),
      animation: { duration: 350, easingFunction: "easeInOutQuad" },
    });
  } catch (_) { /* ignore */ }
}

function wireSideTabs() {
  document.querySelectorAll(".ca-side-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.side;
      document.querySelectorAll(".ca-side-tab").forEach((t) => t.classList.toggle("is-active", t.dataset.side === name));
      document.getElementById("sidePersons").classList.toggle("is-active", name === "persons");
      document.getElementById("sideTimeline").classList.toggle("is-active", name === "timeline");
      document.getElementById("sideDetails").classList.toggle("is-active", name === "details");
    });
  });
}

function setSuggestOpen(open) {
  const page = document.getElementById("caPage") || document.querySelector(".ca-page");
  page?.classList.toggle("is-suggest-open", !!open);
  if (!open) suggestBox?.classList.add("hidden");
}

function hideSuggestions() {
  suggestBox?.classList.add("hidden");
  setSuggestOpen(false);
}

function wireSearch() {
  document.getElementById("searchForm").addEventListener("submit", (e) => {
    e.preventDefault();
    hideSuggestions();
    quickAnalyze();
  });
  document.getElementById("caSearchCancelBtn")?.addEventListener("click", () => {
    collapseSearch();
  });
  companyInput.addEventListener("input", () => {
    pendingUid = "";
    clearTimeout(suggestTimer);
    const q = companyInput.value.trim();
    if (q.length < 2) {
      hideSuggestions();
      return;
    }
    suggestTimer = setTimeout(() => fetchSuggestions(q), 280);
  });
  document.addEventListener("click", (e) => {
    if (!suggestBox.contains(e.target) && e.target !== companyInput) {
      hideSuggestions();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideSuggestions();
  });
}

/** Hide search form — company identity lives only in the firm bar. */
function collapseSearch() {
  const bar = document.getElementById("caSearchBar");
  if (!bar) return;
  bar.classList.add("is-collapsed");
  document.getElementById("caSearchCancelBtn")?.classList.add("hidden");
  hideSuggestions();
  syncRecentVisibility();
}

/** Reveal search form for a new query (results stay visible underneath). */
function expandSearch({ focus = false } = {}) {
  const bar = document.getElementById("caSearchBar");
  if (!bar) return;
  bar.classList.remove("is-collapsed");
  const cancel = document.getElementById("caSearchCancelBtn");
  if (cancel) {
    cancel.classList.toggle("hidden", caResults.classList.contains("hidden"));
  }
  bar.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (focus) {
    companyInput.focus();
    companyInput.select();
  }
  syncRecentVisibility();
}

const CA_RECENT_KEY = "lynx_ca_search_history";
const CA_RECENT_MAX = 10;

function wireRecentSearches() {
  document.getElementById("caRecentClearBtn")?.addEventListener("click", () => {
    try { localStorage.removeItem(CA_RECENT_KEY); } catch (_) {}
    renderRecentSearches();
  });
}

function currentMemberLabel() {
  const u = window.__lynxUser;
  if (!u) return "Team";
  return u.display_name || u.username || "Team";
}

function getRecentSearches() {
  try {
    const raw = JSON.parse(localStorage.getItem(CA_RECENT_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch (_) {
    return [];
  }
}

function rememberSearch(company) {
  if (!company) return;
  const name = (company.name || "").trim();
  const uid = (company.uid || "").trim();
  if (!name && !uid) return;
  const key = (uid || name).toLowerCase();
  const entry = {
    name,
    uid,
    by: currentMemberLabel(),
    at: new Date().toISOString(),
  };
  const next = [entry, ...getRecentSearches().filter((e) => ((e.uid || e.name || "").toLowerCase() !== key))].slice(0, CA_RECENT_MAX);
  try {
    localStorage.setItem(CA_RECENT_KEY, JSON.stringify(next));
  } catch (_) { /* quota */ }
  renderRecentSearches();
}

function formatRecentWhen(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return "gerade eben";
  if (mins < 60) return `vor ${mins} Min.`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `vor ${hrs} Std.`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `vor ${days} Tag${days === 1 ? "" : "en"}`;
  return iso.slice(0, 10);
}

function setIdleHome(on) {
  const page = document.getElementById("caPage") || document.querySelector(".ca-page");
  const idle = document.getElementById("caIdleHome");
  const extras = document.getElementById("caIdleExtras");
  page?.classList.toggle("is-idle", !!on);
  idle?.classList.toggle("hidden", !on);
  extras?.classList.toggle("hidden", !on);
  if (on) {
    page?.classList.remove("is-transitioning", "is-analyzing");
    caResults?.classList.remove("ca-results-enter");
  }
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function waitMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Smooth leave idle → show results (Analyse / Autocomplete / Recent). */
async function transitionToResults() {
  const page = document.getElementById("caPage") || document.querySelector(".ca-page");
  const fromIdle = !!page?.classList.contains("is-idle");
  const reduced = prefersReducedMotion();

  hideStatus();
  page?.classList.remove("is-analyzing");

  if (fromIdle && !reduced) {
    page.classList.add("is-transitioning");
    await waitMs(320);
  }

  caResults.classList.remove("hidden");
  setIdleHome(false);
  collapseSearch();

  if (!reduced) {
    caResults.classList.remove("ca-results-enter");
    // force reflow so enter animation restarts
    void caResults.offsetWidth;
    caResults.classList.add("ca-results-enter");
    await waitMs(480);
    caResults.classList.remove("ca-results-enter");
  }
  page?.classList.remove("is-transitioning");

  // Graph was often drawn while results were still hidden — re-fit after layout.
  requestAnimationFrame(() => {
    const nodes = lastGraph?.nodes || lastAnalysis?.nodes || [];
    fitNetworkView(networkInstance, {
      nodeIds: graphFitNodeIds(nodes),
      animation: false,
    });
  });
}

async function transitionToIdle() {
  const page = document.getElementById("caPage") || document.querySelector(".ca-page");
  const reduced = prefersReducedMotion();
  if (!reduced && !caResults.classList.contains("hidden")) {
    caResults.classList.add("ca-results-leave");
    await waitMs(220);
    caResults.classList.remove("ca-results-leave");
  }
  caResults.classList.add("hidden");
  setIdleHome(true);
  expandSearch();
  page?.classList.remove("is-transitioning", "is-analyzing");
}

function syncRecentVisibility() {
  const el = document.getElementById("caRecentSearches");
  if (!el) return;
  const searchOpen = !document.getElementById("caSearchBar")?.classList.contains("is-collapsed");
  const hasItems = getRecentSearches().length > 0;
  el.classList.toggle("hidden", !searchOpen || !hasItems);
}

function renderRecentSearches() {
  const list = document.getElementById("caRecentList");
  if (!list) return;
  const items = getRecentSearches();
  if (!items.length) {
    list.innerHTML = "";
    syncRecentVisibility();
    return;
  }
  list.innerHTML = items.map((e) => `
    <li>
      <button type="button" class="ca-recent-item" data-name="${escHtml(e.name || "")}" data-uid="${escHtml(e.uid || "")}">
        <span class="ca-recent-name">${escHtml(e.name || e.uid || "—")}</span>
        <span class="ca-recent-meta">
          ${e.uid ? `<span class="ca-recent-uid">${escHtml(e.uid)}</span>` : ""}
          <span class="ca-recent-by">${escHtml(e.by || "Team")}</span>
          <span class="ca-recent-when">${escHtml(formatRecentWhen(e.at))}</span>
        </span>
      </button>
    </li>`).join("");
  list.querySelectorAll(".ca-recent-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      companyInput.value = btn.dataset.name || "";
      pendingUid = btn.dataset.uid || "";
      quickAnalyze();
    });
  });
  syncRecentVisibility();
}

async function refreshIdlePulse() {
  const casesEl = document.getElementById("caPulseCases");
  const watchEl = document.getElementById("caPulseWatch");
  if (!casesEl && !watchEl) return;
  try {
    const [casesResp, watchResp] = await Promise.all([
      fetch("/api/company-cases"),
      fetch("/api/watchlist/inbox?limit=1"),
    ]);
    if (casesResp.ok) {
      const data = await casesResp.json();
      const cases = Array.isArray(data.cases) ? data.cases : [];
      const open = cases.filter((c) => {
        const s = String(c.status || "").toLowerCase();
        return s && s !== "closed" && s !== "actioned" && s !== "cleared";
      }).length;
      if (casesEl) casesEl.textContent = String(open);
    } else if (casesEl) {
      casesEl.textContent = "0";
    }
    if (watchResp.ok) {
      const data = await watchResp.json();
      if (watchEl) watchEl.textContent = String(data.total ?? data.items?.length ?? 0);
    } else if (watchEl) {
      watchEl.textContent = "0";
    }
  } catch (_) {
    if (casesEl) casesEl.textContent = "0";
    if (watchEl) watchEl.textContent = "0";
  }
}

function wireIdleHome() {
  const showProfiler = () => {
    const link = document.getElementById("caIdleProfiler");
    if (!link) return;
    link.classList.toggle("hidden", window.__lynxUser?.role !== "admin");
  };
  showProfiler();
  refreshIdlePulse();
  const prev = window.onLynxUserReady;
  window.onLynxUserReady = function (u) {
    if (typeof prev === "function") prev(u);
    showProfiler();
    refreshIdlePulse();
  };
}

async function fetchSuggestions(q) {
  try {
    const resp = await fetch(`/api/hr-network/search?q=${encodeURIComponent(q)}`);
    const data = await resp.json();
    const results = data.results || [];
    if (!results.length) {
      hideSuggestions();
      return;
    }
    suggestBox.innerHTML = results.map((r) => `
      <li><button type="button" data-name="${escHtml(r.name || "")}" data-uid="${escHtml(r.uid || "")}">
        <strong>${escHtml(r.name || "")}</strong>
        <span>${escHtml(r.uid || "")} · ${escHtml(r.legal_seat || r.canton || "")}</span>
      </button></li>`).join("");
    suggestBox.classList.remove("hidden");
    setSuggestOpen(true);
    suggestBox.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        companyInput.value = btn.dataset.name;
        pendingUid = btn.dataset.uid || "";
        hideSuggestions();
        quickAnalyze();
      });
    });
  } catch (_) {
    hideSuggestions();
  }
}

async function ensureCaseLookup(company) {
  if (!company) return null;
  const qs = new URLSearchParams();
  if (company.uid) qs.set("uid", company.uid);
  if (company.ehraid) qs.set("ehraid", String(company.ehraid));
  if (company.name) qs.set("name", company.name);
  try {
    const resp = await fetch(`/api/company-cases/lookup?${qs}`);
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.case || null;
  } catch (_) {
    return null;
  }
}

async function quickAnalyze() {
  const company = companyInput.value.trim();
  const uid = pendingUid.trim();
  if (!company && !uid) return;
  const url = new URL(location.href);
  url.searchParams.delete("tab");
  if (company) url.searchParams.set("company", company);
  else url.searchParams.delete("company");
  if (uid) url.searchParams.set("uid", uid);
  else url.searchParams.delete("uid");
  history.replaceState({}, "", url);

  hideError();
  const page = document.getElementById("caPage") || document.querySelector(".ca-page");
  if (page?.classList.contains("is-idle")) {
    page.classList.add("is-analyzing");
  }
  showStatus("Lade Analyse…");
  document.getElementById("searchBtn").disabled = true;
  try {
    const qs = new URLSearchParams();
    if (company) qs.set("company", company);
    if (uid) qs.set("uid", uid);
    const [resp] = await Promise.all([
      fetch(`/api/hr-network?${qs}`),
    ]);
    const data = await resp.json();
    if (!resp.ok) throw new Error(formatDetail(data.detail) || `HTTP ${resp.status}`);
    currentCompany = data.company;
    lastGraph = data;
    lastAnalysis = data;
    currentCaseHit = await ensureCaseLookup(currentCompany);
    showBranchHintForCompany(currentCompany);
    renderSearchResults(data);
    // Erste Analyse = Ebene 2 (Firma + aktuelle/ehemalige) — Regler daran ausrichten.
    setDeepLevel(Number(data.level) || 2);
    rememberSearch(currentCompany);
    await transitionToResults();
  } catch (err) {
    showError(err.message);
    await transitionToIdle();
  } finally {
    document.getElementById("searchBtn").disabled = false;
  }
}

async function deepAnalyze() {
  if (!currentCompany) return;
  let level = Number(document.getElementById("deepLevelRange")?.value || selectedDeepLevel);
  selectedDeepLevel = level;
  let maxPersonSearches = FULL_PERSON_SEARCHES;

  if (needsHeavyWarning(level)) {
    const choice = await openHeavyWarnModal(level);
    if (!choice || choice === "cancel") return;
    if (choice === "safe") {
      level = Math.min(level, SAFE_MAX_LEVEL);
      maxPersonSearches = SAFE_PERSON_SEARCHES;
      setDeepLevel(level);
    }
  }

  await runDeepAnalyze(level, maxPersonSearches);
}

function companySizeSignals() {
  const data = lastAnalysis || lastGraph || {};
  const persons = data.persons_table || data.persons || [];
  const nodes = data.nodes || [];
  return {
    shab: Number(data.publication_count) || 0,
    persons: persons.length,
    nodes: nodes.length,
  };
}

function isHeavyCompanySize() {
  const s = companySizeSignals();
  return s.shab >= HEAVY_SHAB || s.persons >= HEAVY_PERSONS || s.nodes >= HEAVY_NODES;
}

function needsHeavyWarning(_level) {
  // Only for genuinely large firms — high level alone is fine for small networks
  // (e.g. Mein Shuttle: 3 Personen / 4 Knoten must not open this dialog).
  return isHeavyCompanySize();
}

function updateHeavyCompanyHint() {
  const badge = document.getElementById("caHeavyBadge");
  if (!badge) return;
  const show = !!currentCompany && isHeavyCompanySize();
  badge.classList.toggle("hidden", !show);
}

function setDeepLevel(level) {
  const range = document.getElementById("deepLevelRange");
  if (!range) return;
  range.value = String(level);
  range.dispatchEvent(new Event("input"));
}

function wireHeavyWarnModal() {
  const modal = document.getElementById("caHeavyModal");
  if (!modal) return;
  modal.querySelectorAll("[data-heavy-close]").forEach((el) => {
    el.addEventListener("click", () => closeHeavyWarnModal("cancel"));
  });
  document.getElementById("caHeavySafeBtn")?.addEventListener("click", () => closeHeavyWarnModal("safe"));
  document.getElementById("caHeavyFullBtn")?.addEventListener("click", () => closeHeavyWarnModal("full"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) {
      closeHeavyWarnModal("cancel");
    }
  });
}

function openHeavyWarnModal(level) {
  const modal = document.getElementById("caHeavyModal");
  const body = document.getElementById("caHeavyModalBody");
  const hint = document.querySelector(".ca-heavy-modal-hint");
  if (!modal || !body) return Promise.resolve("cancel");

  const s = companySizeSignals();
  const bits = [];
  if (s.shab) bits.push(`${s.shab} SHAB-Publikationen`);
  if (s.persons) bits.push(`${s.persons} Personen`);
  if (s.nodes) bits.push(`${s.nodes} Graph-Knoten`);
  const sizeLine = bits.join(" · ") || "hohe Komplexität";

  body.textContent =
    `Viele Register-Einträge (${sizeLine}). Ebene ${level} startet zusätzliche Personen-/Firmensuchen ` +
    `über Zefix/SHAB — das kann mehrere Minuten dauern und die APIs belasten.`;
  if (hint) {
    hint.textContent = `Empfehlung: sichere Suche (Ebene ≤ ${SAFE_MAX_LEVEL}, weniger Personensuchen).`;
  }

  modal.classList.remove("hidden");
  document.body.classList.add("ca-heavy-modal-open");
  document.getElementById("caHeavySafeBtn")?.focus();

  return new Promise((resolve) => {
    heavyModalResolver = resolve;
  });
}

function closeHeavyWarnModal(choice) {
  const modal = document.getElementById("caHeavyModal");
  modal?.classList.add("hidden");
  document.body.classList.remove("ca-heavy-modal-open");
  if (heavyModalResolver) {
    const resolve = heavyModalResolver;
    heavyModalResolver = null;
    resolve(choice);
  }
}

async function runDeepAnalyze(level, maxPersonSearches) {
  selectedDeepLevel = level;
  hideNotify();
  const before = graphFingerprint(lastGraph);
  startDeepProgress(level);
  document.getElementById("deepBtn").disabled = true;
  try {
    const resp = await fetch("/api/fraud-network/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        level,
        ad_hoc_company: {
          name: currentCompany.name || companyInput.value.trim(),
          uid: currentCompany.uid || pendingUid || "",
        },
        max_person_searches: maxPersonSearches,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(formatDetail(data.detail) || `HTTP ${resp.status}`);
    await finishDeepProgress();
    lastGraph = data;
    setDeepLevel(Number(data.level) || level);
    renderGraph(data.nodes || [], data.edges || [], "caGraph", (n) => { networkInstance = n; });
    renderPersonsTable(data.persons_table || [], currentCompany);
    const after = graphFingerprint(data);
    const added = {
      nodes: Math.max(0, after.nodes - before.nodes),
      edges: Math.max(0, after.edges - before.edges),
      persons: Math.max(0, after.persons - before.persons),
    };
    const ps = data.stats?.person_search || {};
    const shabBit = ps.searched ? ` · SHAB ${ps.matches || 0} in ${ps.elapsed_seconds || "?"}s` : "";
    if (added.nodes === 0 && added.edges === 0 && added.persons === 0) {
      hideStatus();
      showNotify(
        `Keine neuen Treffer auf Ebene ${level} — Netzwerk unverändert${shabBit}.`,
        { ok: false, sound: true }
      );
    } else {
      const bits = [];
      if (added.nodes) bits.push(`+${added.nodes} Knoten`);
      if (added.persons) bits.push(`+${added.persons} Personen`);
      if (added.edges) bits.push(`+${added.edges} Verbindungen`);
      hideStatus();
      showNotify(
        `Ergebnisse bereit · Ebene ${level}: ${bits.join(" · ")}${shabBit}`,
        { ok: true, sound: true }
      );
    }
  } catch (err) {
    stopDeepProgress();
    showError(err.message);
  } finally {
    document.getElementById("deepBtn").disabled = false;
  }
}

function graphFingerprint(data) {
  const nodes = data?.nodes || [];
  const edges = data?.edges || [];
  const persons = data?.persons_table || data?.persons || [];
  return {
    nodes: nodes.length,
    edges: edges.length,
    persons: persons.length,
  };
}

const LEVEL_ETA_MS = { 1: 6000, 2: 10000, 3: 22000, 4: 38000, 5: 55000 };
let deepProgressTimer = null;
let deepProgressValue = 0;

function startDeepProgress(level) {
  stopDeepProgress();
  deepProgressValue = 4;
  const wrap = document.getElementById("caProgressWrap");
  const bar = document.getElementById("caProgressBar");
  wrap?.classList.remove("hidden");
  wrap?.setAttribute("aria-hidden", "false");
  if (bar) bar.style.width = `${deepProgressValue}%`;
  showStatus(`Suche Ebene ${level}…`);

  const eta = LEVEL_ETA_MS[level] || 25000;
  const tickMs = 280;
  const targetBeforeDone = 92;
  deepProgressTimer = setInterval(() => {
    // Ease toward ~92% over estimated duration
    const step = Math.max(0.35, (targetBeforeDone - deepProgressValue) * (tickMs / eta) * 1.4);
    deepProgressValue = Math.min(targetBeforeDone, deepProgressValue + step);
    if (bar) bar.style.width = `${deepProgressValue.toFixed(1)}%`;
  }, tickMs);
}

function finishDeepProgress() {
  return new Promise((resolve) => {
    stopDeepProgress(false);
    const wrap = document.getElementById("caProgressWrap");
    const bar = document.getElementById("caProgressBar");
    deepProgressValue = 100;
    if (bar) bar.style.width = "100%";
    setTimeout(() => {
      wrap?.classList.add("hidden");
      wrap?.setAttribute("aria-hidden", "true");
      if (bar) bar.style.width = "0%";
      deepProgressValue = 0;
      resolve();
    }, 320);
  });
}

function stopDeepProgress(hide = true) {
  if (deepProgressTimer) {
    clearInterval(deepProgressTimer);
    deepProgressTimer = null;
  }
  if (hide) {
    const wrap = document.getElementById("caProgressWrap");
    const bar = document.getElementById("caProgressBar");
    wrap?.classList.add("hidden");
    wrap?.setAttribute("aria-hidden", "true");
    if (bar) bar.style.width = "0%";
    deepProgressValue = 0;
  }
}

let notifyTimer = null;
let notifyHideTimer = null;

/** Short ready-chime (Web Audio) — no external sound file. */
function playReadyChime() {
  try {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const now = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(0.07, now);
    master.connect(ctx.destination);
    const beep = (freq, t0, dur) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.setValueAtTime(freq, t0);
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(1, t0 + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      o.connect(g);
      g.connect(master);
      o.start(t0);
      o.stop(t0 + dur + 0.02);
    };
    beep(784, now, 0.12);
    beep(1175, now + 0.1, 0.16);
    setTimeout(() => { try { ctx.close(); } catch (_) { /* ignore */ } }, 500);
  } catch (_) { /* autoplay / unsupported */ }
}

function showNotify(msg, { ok = false, sound = false, duration = 4800 } = {}) {
  const el = document.getElementById("caNotify");
  if (!el) return;
  clearTimeout(notifyTimer);
  clearTimeout(notifyHideTimer);
  el.textContent = msg;
  el.classList.toggle("is-ok", !!ok);
  el.classList.toggle("is-warn", !ok);
  el.classList.remove("hidden", "ca-toast-out");
  // restart enter animation
  void el.offsetWidth;
  el.classList.add("ca-toast-in");
  if (sound) playReadyChime();
  notifyTimer = setTimeout(() => {
    el.classList.remove("ca-toast-in");
    el.classList.add("ca-toast-out");
    notifyHideTimer = setTimeout(() => {
      el.classList.add("hidden");
      el.classList.remove("ca-toast-out");
    }, 240);
  }, duration);
}

function hideNotify() {
  clearTimeout(notifyTimer);
  clearTimeout(notifyHideTimer);
  const el = document.getElementById("caNotify");
  if (!el) return;
  el.classList.add("hidden");
  el.classList.remove("ca-toast-in", "ca-toast-out", "is-ok", "is-warn");
}

function renderSearchResults(data) {
  const company = data.company || {};
  renderFirmBar(company, data);
  const warnings = [...(data.warnings || [])];
  if (currentCaseHit) {
    const st = currentCaseHit.status || "confirmed";
    warnings.unshift(
      st === "under_review"
        ? `Bereits in Prüfung (Akte #${currentCaseHit.id}, ${currentCaseHit.opened_by || ""})`
        : `Bestätigter Fraud-Fall (Akte #${currentCaseHit.id || currentCaseHit.case_id})`
    );
  }
  renderWarnings(warnings);
  renderPersonsTable(data.persons_table || data.persons || [], company);
  renderTimeline(data.recent_publications || [], data.mutation_analysis);
  renderDetails(company, data);
  renderGraph(data.nodes || [], data.edges || [], "caGraph", (n) => { networkInstance = n; });
  updateHeavyCompanyHint();
}

function renderFirmBar(company, data) {
  const card = document.getElementById("companyCard");
  const canton = company.canton || "";
  const status = String(company.status || "").toUpperCase();
  const statusClass = statusTone(status);
  const statusText = statusDisplayLabel(status);
  const seat = [cantonDisplayName(canton) || canton, company.legal_seat].filter(Boolean).join(" · ");
  const formShort = shortenLegalForm(company.legal_form);
  const onCase = !!currentCaseHit;
  const caseStatus = currentCaseHit?.status || "";
  const hr = safeHttpUrl(company.cantonal_excerpt_url);
  const metaCount = [company.uid, seat, formShort, data.publication_count != null].filter(Boolean).length;

  card.classList.toggle("is-on-fraudlist", onCase && caseStatus !== "under_review");
  card.innerHTML = `
    <div class="ca-firm-top">
      <div class="ca-firm-identity">
        <h2 class="ca-firm-name">${escHtml(typeof anon === "function" ? anon(company.name || "", "company") : (company.name || ""))}</h2>
        <div class="ca-firm-badges">
          ${status ? `<span class="ca-status ca-status-${statusClass}" title="${escHtml(status)}"><span class="ca-status-dot" aria-hidden="true"></span>${escHtml(statusText)}</span>` : ""}
          ${onCase
            ? `<span class="ca-fraudlist-badge" title="Firmenakte">${caseStatus === "under_review" ? "In Prüfung" : "Fraud-Fall"}${currentCaseHit.id ? ` #${currentCaseHit.id}` : ""}</span>`
            : ""}
        </div>
      </div>
      <div class="ca-firm-actions">
        <div class="ca-firm-tools">
          <button type="button" class="ca-tool-link" id="caChangeSearchBtn" title="Andere Firma suchen">Suche ändern</button>
          ${hr ? `<a class="ca-tool-link" href="${escHtml(hr)}" target="_blank" rel="noopener">HR-Auszug ↗</a>` : ""}
          <button type="button" class="ca-tool-link ca-profiler-enter hidden" id="profilerEnterBtn" title="Fall-Cockpit: Netzwerk, Signale, Konten, Screening">Profiler</button>
        </div>
        ${onCase && currentCaseHit.id
          ? `<a class="btn-nav ca-btn-fraud is-listed ca-firm-primary" href="/cases/${currentCaseHit.id}">Zur Akte</a>`
          : `<button type="button" class="btn-nav ca-btn-fraud ca-firm-primary" id="openCaseBtn" title="Akte jederzeit möglich — Netzwerkprüfung empfohlen">Akte eröffnen</button>`}
      </div>
    </div>
    <div class="ca-firm-meta" style="--ca-meta-cols:${Math.max(metaCount, 1)}">
      ${company.uid ? `<span class="ca-meta ca-meta-uid" title="UID"><span class="ca-meta-label">UID</span><strong>${escHtml(typeof anon === "function" ? anon(company.uid, "uid") : company.uid)}</strong></span>` : ""}
      ${seat ? `<span class="ca-meta ca-meta-seat" title="Sitz"><span class="ca-meta-label">Sitz</span><span class="ca-meta-seat-value">${canton ? cantonWappenHtml(canton, 16) : ""}<strong>${escHtml(typeof anon === "function" ? anon(seat, "place") : seat)}</strong></span></span>` : ""}
      ${formShort ? `<span class="ca-meta ca-meta-form">
        <span class="ca-meta-label-row">
          <span class="ca-meta-label">Form</span>
          <button type="button" class="ca-form-info" aria-label="Rechtsformen-Legende" aria-describedby="caFormLegend">
            <span class="ca-form-info-icon" aria-hidden="true">i</span>
            <span class="ca-form-legend" id="caFormLegend" role="tooltip">${legalFormLegendHtml(formShort)}</span>
          </button>
        </span>
        <strong title="${escHtml(company.legal_form || "")}">${escHtml(formShort)}</strong>
      </span>` : ""}
      ${data.publication_count != null ? `<span class="ca-meta ca-meta-shab" title="SHAB-Publikationen"><span class="ca-meta-label">SHAB</span><strong>${escHtml(String(data.publication_count))}</strong></span>` : ""}
    </div>
    ${!onCase ? `<p class="ca-open-case-hint" id="openCaseHint">${escHtml(openCaseSoftHint())}</p>` : ""}`;
  wireWappenImages(card);
  document.getElementById("caChangeSearchBtn")?.addEventListener("click", () => expandSearch({ focus: true }));
  document.getElementById("openCaseBtn")?.addEventListener("click", openCompanyCase);
  document.getElementById("profilerEnterBtn")?.addEventListener("click", () => {
    if (typeof window.openProfiler === "function") window.openProfiler();
  });
  if (typeof window.refreshProfilerAdminUi === "function") window.refreshProfilerAdminUi();
}

/** Soft process rule (C): always allow open; network is a bonus before/after the call. */
function openCaseSoftHint() {
  const hasGraph = !!(networkInstance || (lastAnalysis?.nodes || []).length);
  if (hasGraph) {
    return "Akte anlegen → Status «In Prüfung». Typisch: nach (oder vor) dem Kundengespräch bestätigen.";
  }
  return "Akte jederzeit möglich. Netzwerk ist ein Bonus zur Vorbereitung — nicht Pflicht vor dem Anruf.";
}

function statusTone(status) {
  if (/ACTIVE|EXISTIEREND|AKTIV/.test(status)) return "ok";
  if (/LIQUID|AUFLÖS|AUFLOES|IN_AUFL/.test(status)) return "warn";
  if (/DELETE|GELÖSCHT|GELOESCHT|CANCEL|RADIERT/.test(status)) return "bad";
  return "muted";
}

function statusDisplayLabel(status) {
  if (/ACTIVE|EXISTIEREND|AKTIV/.test(status)) return "Aktiv";
  if (/LIQUID|AUFLÖS|AUFLOES|IN_AUFL/.test(status)) return "In Auflösung";
  if (/DELETE|GELÖSCHT|GELOESCHT|CANCEL|RADIERT/.test(status)) return "Gelöscht";
  return status || "—";
}

function shortenLegalForm(form) {
  if (!form) return "";
  return String(form)
    .replace(/Gesellschaft mit beschränkter Haftung/gi, "GmbH")
    .replace(/Aktiengesellschaft/gi, "AG")
    .replace(/Einzelunternehmen/gi, "EU")
    .replace(/Kollektivgesellschaft/gi, "KlG")
    .replace(/Kommanditgesellschaft/gi, "KmG")
    .replace(/Genossenschaft/gi, "Gen.");
}

const LEGAL_FORM_LEGEND = [
  ["EU", "Einzelunternehmen — eine natürliche Person, volle persönliche Haftung"],
  ["GmbH", "Gesellschaft mit beschränkter Haftung — Stammkapital, Haftung begrenzt"],
  ["AG", "Aktiengesellschaft — Aktienkapital, Haftung auf Gesellschaft beschränkt"],
  ["KlG", "Kollektivgesellschaft — mind. zwei Gesellschafter, unbeschränkte Haftung"],
  ["KmG", "Kommanditgesellschaft — Komplementäre unbeschränkt, Kommanditäre beschränkt"],
  ["Gen.", "Genossenschaft — gemeinsamer Zweck, Mitgliederstruktur"],
  ["Verein", "Verein — ideeller Zweck, i. d. R. ohne Kapitalanteil"],
  ["Stiftung", "Stiftung — zweckgebundenes Vermögen, keine Eigentümer"],
];

function legalFormLegendHtml(highlight) {
  const rows = LEGAL_FORM_LEGEND.map(([abbr, desc]) => {
    const active = abbr === highlight ? " is-current" : "";
    return `<li class="ca-form-legend-row${active}"><strong>${escHtml(abbr)}</strong><span>${escHtml(desc)}</span></li>`;
  }).join("");
  return `<span class="ca-form-legend-title">Rechtsformen (CH)</span><ul class="ca-form-legend-list">${rows}</ul>`;
}

function renderWarnings(warnings) {
  const wbox = document.getElementById("warningsBox");
  if (!warnings.length) {
    wbox.classList.add("hidden");
    wbox.innerHTML = "";
    return;
  }
  wbox.innerHTML = warnings.map((w) => {
    const fraud = /fraud|prüfung|akte/i.test(String(w));
    return `<span class="ca-warn-pill${fraud ? " is-fraudlist" : ""}">${escHtml(w)}</span>`;
  }).join("");
  wbox.classList.remove("hidden");
}

function genderMark(gender) {
  if (gender !== "f" && gender !== "m") return "";
  const title = gender === "f" ? "weiblich (HR-Titel)" : "männlich (HR-Titel)";
  const letter = gender === "f" ? "W" : "M";
  return `<span class="ca-gender ca-gender-${gender}" title="${title}">${letter}</span>`;
}

/** Person node icon: silhouette; case/watchlist = badge overlay (keeps figure visible). */
function personSilhouetteIcon(gender, isFormer, caseInvolved) {
  const g = gender === "f" || gender === "m" ? gender : null;
  let fill = "#1e293b";
  let ring = "#64748b";
  let figure = "#e2e8f0";
  if (caseInvolved) {
    fill = "#9a3412";
    ring = "#fbbf24";
    figure = "#fff7ed";
  } else if (isFormer) {
    fill = "#1a1a1c";
    ring = "#3f3f46";
    figure = "#52525b";
  } else if (g === "f") {
    fill = "#1c1917";
    ring = "#a8a29e";
    figure = "#f5f5f4";
  } else if (g === "m") {
    fill = "#0f172a";
    ring = "#94a3b8";
    figure = "#e2e8f0";
  }
  // Pure-shape SVG (no <text>) so circularImage never fails to decode
  const badge = caseInvolved
    ? `<circle cx="48" cy="14" r="12" fill="#f59e0b" stroke="#78350f" stroke-width="2"/>
       <path d="M48 7 L55 20 H41 Z" fill="#1c1917"/>
       <rect x="46.5" y="11" width="3" height="5" rx="0.5" fill="#f59e0b"/>
       <circle cx="48" cy="18.5" r="1.3" fill="#f59e0b"/>`
    : "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
    <circle cx="32" cy="32" r="29" fill="${fill}"/>
    <circle cx="32" cy="32" r="28" fill="none" stroke="${ring}" stroke-width="${caseInvolved ? 3.5 : 1.5}"/>
    <circle cx="32" cy="24" r="9" fill="${figure}"/>
    <path d="M14 52c3.5-11 12-16 18-16s14.5 5 18 16" fill="${figure}"/>
    ${badge}
  </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function formatDateCH(value) {
  if (value == null || value === "" || value === "—") return "—";
  const s = String(value).trim();
  if (/^\d{2}\.\d{2}\.\d{4}/.test(s)) return s.slice(0, 10);
  const iso = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) return `${iso[3]}.${iso[2]}.${iso[1]}`;
  const t = Date.parse(s);
  if (!Number.isNaN(t)) {
    const d = new Date(t);
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    return `${dd}.${mm}.${d.getFullYear()}`;
  }
  return s;
}

function formatDatesInText(text) {
  return String(text || "").replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (_, y, m, d) => `${d}.${m}.${y}`);
}

function watchButtonHtml({ watched = false, name = "", residence = "" } = {}) {
  const eye = `<svg class="ca-watch-ico" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>`;
  const check = `<svg class="ca-watch-ico" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M20 6L9 17l-5-5"/></svg>`;
  if (watched) {
    return `<button type="button" class="ca-person-watch is-on" disabled title="Bereits auf der Watchlist" aria-label="Bereits auf der Watchlist">${check}</button>`;
  }
  return `<button type="button" class="ca-person-watch" data-watch="${escHtml(name)}" data-res="${escHtml(residence || "")}" title="Auf Watchlist setzen" aria-label="Auf Watchlist setzen">${eye}</button>`;
}

/** SHAB/HR: Ausländer → Staatsangehörigkeit; Schweizer → Heimatort; selten → staatenlos. */
function personNationalityInfo(p) {
  const raw = (p.nationality || "").trim();
  if (raw) {
    if (/^staatenlose?(?:r|n)?$/i.test(raw) || /^apatride$/i.test(raw)) {
      return { text: "Staatenlos", inferred: false };
    }
    return { text: raw, inferred: false };
  }
  if ((p.heimatort || "").trim()) {
    return { text: "Schweiz", inferred: true };
  }
  return { text: "", inferred: false };
}

function personFact(label, value, { hint = "", skipAnon = false } = {}) {
  if (value == null || String(value).trim() === "") return "";
  const raw = String(value).trim();
  const display = skipAnon || typeof anon !== "function" ? raw : anon(raw, "place");
  const hintHtml = hint
    ? ` <span class="ca-fact-hint" title="${escHtml(hint)}">*</span>`
    : "";
  return `<div class="ca-fact">
    <dt>${escHtml(label)}${hintHtml}</dt>
    <dd>${escHtml(display)}</dd>
  </div>`;
}

function renderPersonsTable(persons) {
  const box = document.getElementById("personsBox");
  const list = persons || [];
  if (!list.length) {
    box.innerHTML = `<p class="hr-empty">Keine Personen erkannt.</p>`;
    return;
  }
  const current = list.filter((p) => p.status !== "former");
  const former = list.filter((p) => p.status === "former");

  const renderPersonItem = (p) => {
    const name = p.name || "";
    const showName = typeof anon === "function" ? anon(name, "person") : name;
    const roles = dedupeRoleLabels(p.roles || []);
    const nat = personNationalityInfo(p);
    const residence = (p.residence || "").trim();
    const heimatort = (p.heimatort || "").trim();
    const caseFlag = p.case_involved || p.on_watchlist;
    const from = formatDateCH(p.first_seen || p.source_date);
    const to = formatDateCH(p.last_seen);
    let periodLabel = "";
    if (p.status === "former") {
      if (from !== "—" && to !== "—") periodLabel = `${from} – ${to}`;
      else if (to !== "—") periodLabel = `bis ${to}`;
      else if (from !== "—") periodLabel = from;
    } else if (from !== "—") {
      periodLabel = `seit ${from}`;
    }
    const natHint = nat.inferred
      ? "Im Handelsregister steht bei Schweizern der Heimatort statt der Staatsangehörigkeit"
      : "";

    const facts = [
      personFact("Staatsangehörigkeit", nat.text, { hint: natHint, skipAnon: true }),
      personFact("Wohnort", residence),
      personFact("Heimatort", heimatort),
      personFact(p.status === "former" ? "Zeitraum" : "Erfasst", periodLabel, { skipAnon: true }),
    ].filter(Boolean);

    return `<li class="ca-person-row${caseFlag ? " is-case-flagged" : ""}">
      <div class="ca-person-top">
        <strong class="ca-person-name">${genderMark(p.gender || inferGenderFromRoles(p.roles))}<span>${escHtml(showName)}</span>
          ${caseFlag ? `<span class="ca-case-pill" title="${escHtml(p.case_flag_label || "Fall")}">Fall</span>` : ""}
        </strong>
        ${watchButtonHtml({ watched: !!caseFlag, name, residence })}
      </div>
      ${roles.length ? `<p class="ca-person-roles">${roles.map((r) => escHtml(r)).join(" · ")}</p>` : ""}
      ${facts.length ? `<dl class="ca-person-facts">${facts.join("")}</dl>` : ""}
    </li>`;
  };

  let html = "";
  if (current.length) {
    html += `<div class="ca-person-group">
      <div class="fraud-group-label">Aktuell (${current.length})</div>
      <ul class="ca-persons-list">${current.map(renderPersonItem).join("")}</ul>
    </div>`;
  }
  if (former.length) {
    html += `<details class="ca-person-group ca-person-group-former" open>
      <summary class="ca-former-summary">
        <span class="fraud-group-label">Ehemalig (${former.length})</span>
      </summary>
      <ul class="ca-persons-list ca-persons-former">${former.map(renderPersonItem).join("")}</ul>
    </details>`;
  }
  if ([...current, ...former].some((p) => personNationalityInfo(p).inferred)) {
    html += `<p class="ca-person-footnote">* Schweiz abgeleitet aus Heimatort (HR-Konvention)</p>`;
  }
  box.innerHTML = html || `<p class="hr-empty">Keine Personen erkannt.</p>`;
  box.querySelectorAll("[data-watch]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const resp = await fetch("/api/watched-persons", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: btn.dataset.watch, residence: btn.dataset.res || null }),
      });
      await resp.json();
      btn.outerHTML = watchButtonHtml({ watched: true });
    });
  });
}


function dedupeRoleLabels(roles) {
  const seen = new Set();
  const out = [];
  for (const raw of roles || []) {
    const short = shortenEdgeLabel(raw);
    const key = (short || String(raw)).toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(String(raw).trim());
  }
  return out;
}

function mutationLabels(pub) {
  if (pub.types_de && pub.types_de.length) return pub.types_de;
  return (pub.types || []).map((k) => MUTATION_LABELS[k] || k.replace(/\./g, " "));
}

function timelineTone(labels) {
  const joined = labels.join(" ").toLowerCase();
  if (/organ|geschäfts|gesellschafter|ausgeschieden/.test(joined) || labels.some((l) => /organ/i.test(l))) {
    return "organ";
  }
  if (/sitz|adress|name|zweck|fusion|übernahme|vermögen|liquidation|löschung/.test(joined)) {
    return "struct";
  }
  if (/neu|gründung|eintragung/.test(joined)) return "birth";
  return "neutral";
}

function renderTimeline(pubs, analysis) {
  const box = document.getElementById("timelineBox");
  if (!pubs.length) {
    box.innerHTML = `<p class="hr-empty">Keine SHAB-Ereignisse.</p>`;
    return;
  }
  let html = "";
  if (analysis) {
    html += `<p class="ca-timeline-summary">${escHtml(formatDatesInText(analysis))}</p>`;
  }
  html += `<ol class="ca-timeline">`;
  let lastYear = "";
  for (const pub of pubs) {
    const labels = mutationLabels(pub);
    const tone = timelineTone(labels.concat([pub.message_short || ""]));
    const title = labels.length ? labels.join(" · ") : "SHAB-Meldung";
    const rawDate = pub.date || "";
    const dateCh = formatDateCH(rawDate);
    const year = String(rawDate).slice(0, 4);
    if (year && year !== lastYear && /^\d{4}$/.test(year)) {
      html += `<li class="ca-timeline-year-break" aria-hidden="true"><span>${escHtml(year)}</span></li>`;
      lastYear = year;
    }
    html += `<li class="ca-timeline-item ca-tone-${tone}">
      <div class="ca-timeline-rail" aria-hidden="true"></div>
      <div class="ca-timeline-card">
        <time class="ca-timeline-date" datetime="${escHtml(rawDate)}">${escHtml(dateCh)}</time>
        <strong class="ca-timeline-title">${escHtml(title)}</strong>
        ${pub.message_short
          ? `<button type="button" class="ca-timeline-toggle">Details</button>
             <p class="ca-timeline-msg hidden">${escHtml(formatDatesInText(pub.message_short))}</p>`
          : ""}
      </div>
    </li>`;
  }
  html += `</ol>`;
  box.innerHTML = html;
  box.querySelectorAll(".ca-timeline-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const msg = btn.nextElementSibling;
      const open = !msg.classList.contains("hidden");
      msg.classList.toggle("hidden", open);
      btn.textContent = open ? "Details" : "Weniger";
    });
  });
}

function renderDetails(company, data) {
  const box = document.getElementById("detailsBox");
  const addrHistory = addressHistoryFromPubs(data?.recent_publications || []);
  const addressHtml = company.address
    ? `<span class="ca-detail-address">${escHtml(company.address)}</span>${
        addrHistory.length
          ? `<button type="button" class="ca-addr-info" aria-label="Frühere Adressen" aria-expanded="false">
              <span class="ca-addr-info-icon" aria-hidden="true">i</span>
              <span class="ca-addr-legend" role="tooltip">${addressHistoryLegendHtml(addrHistory)}</span>
            </button>`
          : ""
      }`
    : null;
  const rows = [
    ["Adresse", addressHtml],
    ["Kapital", company.capital],
    ["Zweck", company.purpose_short],
    ["EHRAID", company.ehraid],
    ["Zefix", (() => {
      const z = safeHttpUrl(company.zefix_url);
      return z ? `<a href="${escHtml(z)}" target="_blank" rel="noopener">Öffnen ↗</a>` : null;
    })()],
  ];
  box.innerHTML = `<dl class="ca-details-dl">${rows
    .filter(([, v]) => v)
    .map(([k, v]) => {
      const isHtml = typeof v === "string" && (v.startsWith("<a") || v.includes("ca-detail-address") || v.includes("ca-addr-info"));
      return `<dt>${escHtml(k)}</dt><dd class="${k === "Adresse" ? "ca-details-addr-dd" : ""}">${isHtml ? v : escHtml(String(v))}</dd>`;
    })
    .join("")}</dl>`;
  wireAddrInfoTooltips(box);
}

function wireAddrInfoTooltips(root) {
  root.querySelectorAll(".ca-addr-info").forEach((btn) => {
    const tip = btn.querySelector(".ca-addr-legend");
    if (!tip) return;
    const place = () => {
      tip.classList.add("is-open");
      btn.setAttribute("aria-expanded", "true");
      const r = btn.getBoundingClientRect();
      const tipW = Math.min(22 * 16, window.innerWidth - 24);
      let left = r.left;
      if (left + tipW > window.innerWidth - 12) left = window.innerWidth - tipW - 12;
      if (left < 12) left = 12;
      const spaceAbove = r.top;
      tip.style.position = "fixed";
      tip.style.width = `${tipW}px`;
      tip.style.left = `${left}px`;
      tip.style.right = "auto";
      if (spaceAbove > 180) {
        tip.style.bottom = `${window.innerHeight - r.top + 8}px`;
        tip.style.top = "auto";
      } else {
        tip.style.top = `${r.bottom + 8}px`;
        tip.style.bottom = "auto";
      }
    };
    const hide = () => {
      tip.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    };
    btn.addEventListener("mouseenter", place);
    btn.addEventListener("focus", place);
    btn.addEventListener("mouseleave", hide);
    btn.addEventListener("blur", hide);
  });
}

/** Only SHAB address / seat mutations — extract address text only. */
function addressHistoryFromPubs(pubs) {
  const out = [];
  const seen = new Set();
  for (const pub of pubs || []) {
    if (!isAddressOnlyMutation(pub)) continue;
    const rawMsg = repairMojibake(decodeBasicEntities(pub.message_short || ""));
    const text = extractAddressSnippet(rawMsg);
    if (!text) continue;
    const date = pub.date || "";
    const title = addressMutationTitle(pub);
    const key = `${date}|${text.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ date, title, text });
  }
  return out;
}

function isAddressOnlyMutation(pub) {
  const types = pub.types || [];
  const typesDe = pub.types_de || [];
  const keys = types.join(" ").toLowerCase();
  const labels = typesDe.join(" ").toLowerCase();
  // Must mention address/seat — not purpose-only
  const hasAddr =
    /adress|sitzverleg|domizil/.test(keys) ||
    /adress|sitzverleg|domizil|\bsitz\b/.test(labels);
  if (!hasAddr) return false;
  // If the only DE labels are zweck-related, skip
  const addrLabels = typesDe.filter((l) => /adress|sitz|domizil/i.test(l));
  if (typesDe.length && !addrLabels.length && /zweck/i.test(labels) && !/adress|sitz/i.test(keys)) {
    return false;
  }
  return true;
}

function addressMutationTitle(pub) {
  const labels = (pub.types_de || []).filter((l) => /adress|sitz|domizil/i.test(l));
  if (labels.length) return labels.join(" · ");
  const keys = (pub.types || []).filter((k) => /adress|sitz|domizil/i.test(k));
  if (keys.length) return keys.map((k) => MUTATION_LABELS[k] || "Adressänderung").join(" · ");
  return "Adressänderung";
}

function decodeBasicEntities(s) {
  return String(s || "")
    .replace(/&uuml;/gi, "ü")
    .replace(/&auml;/gi, "ä")
    .replace(/&ouml;/gi, "ö")
    .replace(/&Uuml;/gi, "Ü")
    .replace(/&Auml;/gi, "Ä")
    .replace(/&Ouml;/gi, "Ö")
    .replace(/&eacute;/gi, "é")
    .replace(/&egrave;/gi, "è")
    .replace(/&nbsp;/gi, " ")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCharCode(parseInt(h, 16)));
}

function repairMojibake(str) {
  let s = String(str || "");
  // Common UTF-8→Latin1 artifacts
  if (/Ã.|Ä[¼¤¶]|Â./.test(s)) {
    try {
      const bytes = Uint8Array.from({ length: s.length }, (_, i) => s.charCodeAt(i) & 0xff);
      const out = new TextDecoder("utf-8").decode(bytes);
      if (out && !out.includes("\uFFFD")) s = out;
    } catch (_) { /* keep */ }
  }
  return s
    .replace(/Ä¼/g, "ü")
    .replace(/Ä¤/g, "ä")
    .replace(/Ä¶/g, "ö")
    .replace(/Ã¼/g, "ü")
    .replace(/Ã¤/g, "ä")
    .replace(/Ã¶/g, "ö")
    .replace(/Ã©/g, "é")
    .replace(/Ã¨/g, "è");
}

function extractAddressSnippet(message) {
  let text = repairMojibake(decodeBasicEntities(message)).replace(/\s+/g, " ").trim();
  if (!text) return "";
  // Drop company/UID noise often glued into SHAB excerpts
  text = text.replace(/CHE-\d{3}\.\d{3}\.\d{3}/gi, " ").replace(/\s+/g, " ").trim();

  const marked = text.match(
    /(?:domizil\s+neu|neue\s+adresse|domizil|sitz|adresse)\s*[:：]\s*([^.;|]{6,100})/i
  );
  if (marked) {
    const snip = marked[1].trim().replace(/\s*,\s*$/, "");
    if (snip.length >= 6) return snip;
  }
  const plz = text.match(
    /([A-Za-zÄÖÜäöüéèêàâ][^,]{2,40}?\s+\d+[a-zA-Z]?)\s*,?\s*(\d{4})\s+([A-Za-zÄÖÜäöüéèêàâ][\w\-äöüéèêàâ']+(?:\s+[A-Za-zÄÖÜäöüéèêàâ][\w\-äöüéèêàâ']+){0,2})/
  );
  if (plz) return `${plz[1].trim()}, ${plz[2]} ${plz[3].trim()}`;
  // No reliable address → omit (don't dump full SHAB blob)
  return "";
}

function addressHistoryLegendHtml(items) {
  const rows = items.map((it) => {
    const date = formatDateCH(it.date);
    return `<li class="ca-addr-legend-row">
      <time datetime="${escHtml(it.date || "")}">${escHtml(date)}</time>
      <span>${escHtml(it.text)}</span>
    </li>`;
  }).join("");
  return `<span class="ca-addr-legend-title">Frühere Adressen (SHAB)</span>
    <ul class="ca-addr-legend-list">${rows}</ul>`;
}

async function openCompanyCase() {
  if (!currentCompany) return;
  const hasGraph = !!(networkInstance || (lastAnalysis?.nodes || []).length);
  if (!hasGraph) {
    const go = confirm(
      "Netzwerk noch nicht geladen (optional).\n\nAkte trotzdem als «In Prüfung» anlegen?\nTypischer nächster Schritt: Kundengespräch → in der Akte bestätigen."
    );
    if (!go) return;
  }
  const resp = await fetch("/api/company-cases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      company_name: currentCompany.name,
      company_uid: currentCompany.uid || null,
      company_ehraid: currentCompany.ehraid || null,
      company_purpose: currentCompany.purpose_short || currentCompany.purpose || null,
    }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    showError(formatDetail(data.detail) || "Fall eröffnen fehlgeschlagen");
    return;
  }
  currentCaseHit = data;
  showStatus(
    data.already_existed
      ? `Bestehender Fall #${data.id} (${data.status})`
      : `Akte #${data.id} eröffnet — In Prüfung`
  );
  if (lastAnalysis) renderSearchResults(lastAnalysis);
  else if (currentCompany) renderFirmBar(currentCompany, lastAnalysis || {});
  loadOpenTeamCases();
  if (data.id) location.href = `/cases/${data.id}`;
}

/* ── Graph ── */
function collectEdgeRolesByNode(edges) {
  const map = new Map();
  for (const e of edges || []) {
    const short = shortenEdgeLabel(e.label);
    if (!short) continue;
    // Role belongs to the person side of the link (usually "from")
    for (const id of [e.from, e.to]) {
      if (!map.has(id)) map.set(id, new Set());
      map.get(id).add(short);
    }
  }
  return map;
}

function renderGraph(nodes, edges, containerId, setInstance) {
  const container = document.getElementById(containerId);
  if (!container || typeof vis === "undefined") return;
  if (!nodes.length) {
    container.innerHTML = `<p class="hr-empty">Keine Graph-Daten.</p>`;
    return;
  }
  container.style.height = "420px";
  container.innerHTML = "";

  const rolesByNode = collectEdgeRolesByNode(edges);
  const formerIds = new Set(
    nodes.filter((n) => n.type === "person" && n.person_status === "former").map((n) => n.id)
  );

  const visNodes = new vis.DataSet(nodes.map((n) => {
    const isPerson = n.type === "person";
    const isFormer = isPerson && n.person_status === "former";
    const caseInvolved = isPerson && !!(n.case_involved || n.on_watchlist);
    const gender = isPerson ? (n.gender || inferGenderFromRoles(n.roles)) : null;
    let roleLine = "";
    if (isPerson) {
      // Prefer node roles; avoid merging with edge labels (caused GF/Ges. doubles)
      const rawRoles = (n.roles && n.roles.length)
        ? n.roles
        : [...(rolesByNode.get(n.id) || [])];
      const roles = dedupeRoleLabels(rawRoles).map((r) => shortenEdgeLabel(r)).filter(Boolean);
      const unique = [...new Set(roles)];
      roleLine = unique.length ? `\n${unique.join(" · ")}` : "";
    }
    const base = {
      id: n.id,
      label: formatNodeLabel(n) + roleLine,
      title: htmlTitle(buildTooltip(n, gender)),
      color: nodeColor(n),
      font: {
        color: caseInvolved ? "#fde68a" : (isFormer ? "#6b7280" : "#f8fafc"),
        face: "Rajdhani",
        size: n.is_seed ? 15 : (isFormer ? 11 : 13),
        bold: !!(n.is_seed || caseInvolved),
        multi: true,
        vadjust: isPerson ? 2 : 0,
      },
      borderWidth: caseInvolved ? 3 : (n.is_seed ? 3 : (isFormer ? 1.2 : 2)),
      opacity: caseInvolved ? 1 : (isFormer ? 0.38 : 1),
    };
    if (!isPerson) {
      return { ...base, shape: "box", margin: 10 };
    }
    const icon = personSilhouetteIcon(gender, isFormer, caseInvolved);
    return {
      ...base,
      shape: "circularImage",
      image: icon,
      size: caseInvolved ? 38 : (isFormer ? 22 : 30),
      brokenImage: icon,
      // Ensure border color wins even if image decode fails
      color: caseInvolved
        ? { background: "#9a3412", border: "#fbbf24", highlight: { background: "#c2410c", border: "#fde68a" } }
        : base.color,
    };
  }));

  // Continuous edges — no mid-line labels (roles sit under person names)
  const visEdges = new vis.DataSet(edges.map((e, i) => {
    const touchesFormer = formerIds.has(e.from) || formerIds.has(e.to);
    return {
      id: `e${i}`,
      from: e.from,
      to: e.to,
      title: e.label ? htmlTitle(tipPlainToHtml(shortenEdgeLabel(e.label) || e.label)) : undefined,
      arrows: {
        to: { enabled: true, scaleFactor: touchesFormer ? 0.7 : 0.9, type: "arrow" },
      },
      color: {
        color: touchesFormer ? "#4b5563" : "#94a3b8",
        highlight: touchesFormer ? "#6b7280" : "#f87171",
        hover: touchesFormer ? "#6b7280" : "#67e8f9",
        opacity: touchesFormer ? 0.28 : 0.9,
      },
      width: touchesFormer ? 1.15 : 2.4,
      selectionWidth: touchesFormer ? 1.6 : 3.2,
      hoverWidth: touchesFormer ? 1.5 : 3,
      smooth: { type: "continuous", roundness: 0.35 },
    };
  }));

  const net = new vis.Network(container, { nodes: visNodes, edges: visEdges }, {
    autoResize: true,
    height: "100%",
    width: "100%",
    nodes: {
      shadow: { enabled: true, color: "rgba(0,0,0,0.35)", size: 8, x: 0, y: 2 },
    },
    edges: {
      chosen: true,
      font: { size: 0 },
    },
    physics: {
      barnesHut: {
        gravitationalConstant: -12000,
        springLength: 155,
        springConstant: 0.035,
        avoidOverlap: 0.4,
      },
      stabilization: { iterations: 120 },
    },
    interaction: {
      hover: true,
      tooltipDelay: 80,
      hideEdgesOnDrag: false,
      zoomView: true,
      dragView: true,
    },
  });
  setInstance?.(net);
  const seedId = nodes.find((n) => n.is_seed)?.id || null;
  // Mark seed on vis node for later focus helpers
  if (seedId != null) {
    try { visNodes.update({ id: seedId, isSeed: true }); } catch (_) { /* ignore */ }
  }
  scheduleNetworkFit(net, nodes);
}

/** Gender from German HR titles (Gesellschafterin → f). */
function inferGenderFromRoles(roles) {
  const joined = (roles || []).join(" ");
  if (!joined) return null;
  if (/(geschäftsführerin|gesellschafterin|inhaberin|prokuristin|präsidentin|direktorin|liquidatorin|vertreterin|vorsitzende)\b/i.test(joined)) {
    return "f";
  }
  if (/(geschäftsführer|gesellschafter|inhaber|prokurist|präsident|direktor|liquidator|vertreter|vorsitzender)(?!in)\b/i.test(joined)) {
    return "m";
  }
  return null;
}

function formatNodeLabel(n) {
  const raw = String(n.label || "");
  const masked =
    typeof anon === "function"
      ? anon(raw, n.type === "person" ? "person" : "company")
      : raw;
  if (n.type === "person") {
    const warn = (n.case_involved || n.on_watchlist) ? "⚠ " : "";
    if (masked.includes(",")) {
      const [last, first] = masked.split(",").map((s) => s.trim());
      return `${warn}${trunc(last, 16)}\n${trunc(first || "", 18)}`;
    }
    return `${warn}${trunc(masked, 22)}`;
  }
  const base = trunc(masked, 20);
  return n.likely_shell_takeover ? `${base}\n⚠` : base;
}

function nodeColor(n) {
  if (n.is_seed) return { background: "#dc2626", border: "#fecaca" };
  if (n.type === "person" && (n.case_involved || n.on_watchlist)) {
    return { background: "#9a3412", border: "#fbbf24", highlight: { background: "#c2410c", border: "#fde68a" } };
  }
  if (n.type === "person" && n.person_status === "former") {
    return {
      background: "#18181b",
      border: "#3f3f46",
      highlight: { background: "#27272a", border: "#52525b" },
      hover: { background: "#27272a", border: "#52525b" },
    };
  }
  if (n.type === "person") {
    const g = n.gender || inferGenderFromRoles(n.roles);
    if (g === "f") return { background: "#1c1917", border: "#a8a29e" };
    if (g === "m") return { background: "#0f172a", border: "#94a3b8" };
    return { background: "#1e293b", border: "#64748b" };
  }
  if (n.likely_shell_takeover) return { background: "#7c2d12", border: "#fdba74" };
  return { background: "#334155", border: "#94a3b8" };
}

function tipPlainToHtml(text) {
  const lines = String(text || "").split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return "";
  return `<div class="ca-tip"><div class="ca-tip-body">${lines.map((l) => escHtml(l)).join("<br>")}</div></div>`;
}

/** vis-network escapes HTML strings — pass a DOM node so our tip markup renders. */
function htmlTitle(html) {
  if (!html) return undefined;
  const el = document.createElement("div");
  el.className = "ca-tip-root";
  el.innerHTML = html;
  return el;
}

function tipRow(label, value) {
  if (value == null || value === "") return "";
  return `<div class="ca-tip-row"><span class="ca-tip-k">${escHtml(label)}</span><span class="ca-tip-v">${escHtml(String(value))}</span></div>`;
}

function buildTooltip(n, gender) {
  if (n.type === "person") {
    const isFormer = n.person_status === "former";
    const g = gender === "f" ? "weiblich" : gender === "m" ? "männlich" : null;
    const roles = dedupeRoleLabels(n.roles || []).join(" · ");
    const name = typeof anon === "function" ? anon(n.label || "", "person") : (n.label || "");
    const badgeClass = isFormer ? "ca-tip-badge is-former" : "ca-tip-badge is-current";
    const badgeLabel = isFormer ? "Ehemalig" : "Aktuell";
    const nat = personNationalityInfo(n);
    const warn = (n.case_involved || n.on_watchlist)
      ? tipRow("Hinweis", `${n.case_flag_label || "In Fall / Watchlist"}${n.watch_status ? ` (${n.watch_status})` : ""}`)
      : "";
    return `<div class="ca-tip${isFormer ? " ca-tip-former" : ""}">
      <div class="ca-tip-head">
        <strong class="ca-tip-title">${escHtml(name)}</strong>
        <span class="${badgeClass}">${badgeLabel}</span>
      </div>
      <div class="ca-tip-body">
        ${tipRow("Geschlecht", g ? `${g} (HR-Titel)` : "")}
        ${tipRow("Rolle", roles)}
        ${tipRow("Staatsangehörigkeit", nat.text)}
        ${tipRow("Wohnort", n.residence)}
        ${n.heimatort && n.heimatort !== n.residence ? tipRow("Heimatort", n.heimatort) : ""}
        ${warn}
      </div>
    </div>`;
  }
  const name = typeof anon === "function" ? anon(n.label || "", "company") : (n.label || "");
  return `<div class="ca-tip">
    <div class="ca-tip-head"><strong class="ca-tip-title">${escHtml(name)}</strong></div>
    <div class="ca-tip-body">
      ${tipRow("UID", n.uid)}
      ${n.likely_shell_takeover ? tipRow("Hinweis", "Übernahme-Verdacht") : ""}
    </div>
  </div>`;
}

function shortenEdgeLabel(label) {
  const parts = String(label || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((p) => p
      .replace(/Geschäftsführerin|Geschäftsführer/gi, "GF")
      .replace(/Gesellschafterin|Gesellschafter/gi, "Ges.")
      .replace(/Inhaberin|Inhaber/gi, "Inh.")
      .replace(/Zeichnungsberechtigt(?:e[rn])?/gi, "ZB")
      .replace(/Präsidentin|Präsident/gi, "Präs.")
      .replace(/Liquidatorin|Liquidator/gi, "Liq."));
  return [...new Set(parts)].join(" · ").slice(0, 36);
}

function trunc(s, max) {
  s = String(s || "");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** Only allow http(s) URLs in href (blocks javascript: etc.). */
function safeHttpUrl(u) {
  try {
    const x = new URL(String(u || ""), window.location.origin);
    if (x.protocol === "http:" || x.protocol === "https:") return x.href;
  } catch (_) { /* ignore */ }
  return "";
}

function showError(msg) {
  caError.textContent = msg;
  caError.classList.remove("hidden");
  hideStatus();
  hideNotify();
}
function hideError() { caError.classList.add("hidden"); }
function showStatus(msg) {
  const text = document.getElementById("caStatusText");
  if (text) text.textContent = msg;
  else caStatus.textContent = msg;
  caStatus.classList.remove("hidden");
}
function hideStatus() {
  caStatus.classList.add("hidden");
  stopDeepProgress();
}

function formatDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  return JSON.stringify(detail);
}
