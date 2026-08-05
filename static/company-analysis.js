/** Unified company analysis — compact workspace + SHAB timeline. */

const LEVEL_META = {
  1: { title: "Firma + Inhaber", speed: "schnell" },
  2: { title: "Ehemalige + Struktur", speed: "schnell" },
  3: { title: "Weitere Firmen", speed: "mittel" },
  4: { title: "Ehemalige vernetzt", speed: "länger" },
  5: { title: "2. Ring", speed: "länger" },
};

/** Zefix mutationTypes.key → German label (mirrors app/checks/zefix_mutations.py). */
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

/**
 * Visual kind for timeline markers / pills.
 * Only kinds that real Zefix keys (or person chips) can produce.
 */
const MUTATION_KIND = {
  birth: "birth",
  organ: "organ",
  address: "address",
  purpose: "purpose",
  capital: "capital",
  statutes: "statutes",
  name: "name",
  structure: "structure",
  status: "status",
  delete: "delete",
  liquid: "liquid",
  unknown: "unknown",
};

/** Priority for rail-dot color when one publication has several types. */
const MUTATION_KIND_PRIORITY = [
  "delete",
  "liquid",
  "status",
  "structure",
  "organ",
  "name",
  "address",
  "purpose",
  "capital",
  "statutes",
  "birth",
  "unknown",
];

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
wireNetworkViewToggle();
wireHeavyWarnModal();
document.getElementById("deepBtn")?.addEventListener("click", () => deepAnalyze());
document.getElementById("deepForceRefreshBtn")?.addEventListener("click", () => {
  if (!currentCompany) return;
  const level = Number(document.getElementById("deepLevelRange")?.value || selectedDeepLevel);
  runDeepAnalyze(level, FULL_PERSON_SEARCHES, { forceRefresh: true });
});
wireRecentSearches();
renderRecentSearches();
setIdleHome(true);

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

function normalizePurposeCore(purpose) {
  let t = String(purpose || "")
    .toLowerCase()
    .replace(/<[^>]+>/g, " ")
    .replace(/[«»""„]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  // Strip Swiss Handelsregister boilerplate so "Montage" ≠ "Marketing"
  for (let i = 0; i < 4; i++) {
    const next = t
      .replace(/^die\s+gesellschaft\s+bezweckt\s+(die\s+)?/i, "")
      .replace(/^zweck\s+(der\s+gesellschaft\s+)?(ist|sind)\s+(die\s+)?/i, "")
      .replace(/^erbringung\s+von\s+/i, "")
      .replace(/^leistungen?\s+im\s+bereich\s+(der|des|von)\s+/i, "")
      .replace(/^leistungen?\s+aller\s+art,?\s*(insbesondere\s+)?/i, "")
      .replace(/^im\s+bereich\s+(der|des|von)\s+/i, "")
      .trim();
    if (next === t) break;
    t = next;
  }
  return t;
}

function purposeTokens(purpose) {
  const stop = new Set([
    "und", "oder", "von", "der", "die", "das", "dem", "den", "des", "im", "in", "zu", "zur", "zum",
    "mit", "für", "sowie", "insbesondere", "aller", "art", "bereich", "zweck", "gesellschaft",
    "erbringung", "leistungen", "leistung", "bezweckt", "inklusive", "bzw",
  ]);
  return normalizePurposeCore(purpose)
    .split(/[^a-zäöüß0-9]+/i)
    .map((w) => w.trim())
    .filter((w) => w.length >= 4 && !stop.has(w));
}

function purposeMatchesBranch(purpose, branchKey) {
  if (!purpose || !branchKey) return false;
  const a = purposeTokens(purpose);
  const b = purposeTokens(branchKey);
  if (a.length < 2 || b.length < 2) return false;
  const setB = new Set(b);
  const overlap = a.filter((t) => setB.has(t));
  if (overlap.length >= 2) return true;
  const union = new Set([...a, ...b]);
  const jaccard = overlap.length / union.size;
  return overlap.length >= 1 && jaccard >= 0.4;
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
  const total = Number(branchSignal.total_confirmed) || hit.count || 1;
  el.textContent =
    `Ähnlicher Firmenzweck wie ${hit.count} von ${total} bestätigten Fällen ` +
    `(letzte ${branchSignal.months} Monate): «${hit.label.slice(0, 72)}»`;
  el.classList.remove("hidden");
}

function syncForceRefreshBtn() {
  const btn = document.getElementById("deepForceRefreshBtn");
  if (!btn) return;
  // Sichtbar sobald eine Firma geladen ist (bei E4/E5: Force-Refresh ohne Disk-Cache)
  const show = !!currentCompany;
  btn.classList.toggle("hidden", !show);
  btn.disabled = !show;
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
        <span class="fraud-entry-meta">in Prüfung · ${escHtml(c.opened_by)} · ${escHtml(formatDateDisplay(c.opened_at))}${stale}</span>
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
    syncForceRefreshBtn();
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

  // Organigramm: filter person/company cards
  if (getNetworkViewMode() === "board") {
    const board = document.getElementById("caOrgBoard");
    const cards = [...(board?.querySelectorAll("[data-find-text]") || [])];
    cards.forEach((c) => c.classList.remove("is-find-hit", "is-find-active"));
    if (!q) {
      if (meta) meta.textContent = "";
      graphFindHits = [];
      graphFindIndex = 0;
      return;
    }
    const hits = cards.filter((c) => (c.dataset.findText || "").includes(q));
    hits.forEach((c) => c.classList.add("is-find-hit"));
    if (!hits.length) {
      if (meta) meta.textContent = "0";
      graphFindHits = [];
      return;
    }
    if (cycle && graphFindHits.length === hits.length) {
      graphFindIndex = (graphFindIndex + 1) % hits.length;
    } else {
      graphFindHits = hits;
      graphFindIndex = 0;
    }
    const active = hits[graphFindIndex] || hits[0];
    active.classList.add("is-find-active");
    active.scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (meta) meta.textContent = `${graphFindIndex + 1}/${hits.length}`;
    return;
  }

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
  return formatDateDisplay(iso);
}

function setIdleHome(on) {
  const page = document.getElementById("caPage") || document.querySelector(".ca-page");
  const idle = document.getElementById("caIdleHome");
  page?.classList.toggle("is-idle", !!on);
  idle?.classList.toggle("hidden", !on);
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

async function fetchSuggestions(q) {
  try {
    const resp = await fetch(`/api/hr-network/search?q=${encodeURIComponent(q)}`);
    const data = await resp.json();
    const results = data.results || [];
    if (!results.length) {
      hideSuggestions();
      return;
    }
    suggestBox.innerHTML = results.map((r) => {
      const cancelled = !!r.is_cancelled;
      const being = !!r.is_being_cancelled;
      let badge = "";
      if (cancelled) {
        const when = r.deletion_date ? ` · gelöscht ${escHtml(formatDateDisplay(r.deletion_date))}` : "";
        badge = `<span class="ca-suggest-badge is-cancelled">Gelöscht${when}</span>`;
      } else if (being) {
        badge = `<span class="ca-suggest-badge is-liquidating">In Auflösung</span>`;
      }
      const cls = [
        cancelled ? "is-cancelled" : "",
        being ? "is-liquidating" : "",
      ].filter(Boolean).join(" ");
      return `<li><button type="button" class="${cls}" data-name="${escHtml(r.name || "")}" data-uid="${escHtml(r.uid || "")}">
        <span class="ca-suggest-main">
          <strong>${escHtml(r.name || "")}</strong>
          ${badge}
        </span>
        <span>${escHtml(r.uid || "")} · ${escHtml(r.legal_seat || r.canton || "")}</span>
      </button></li>`;
    }).join("");
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
    let data;
    try {
      const parsed = await fetchJson(`/api/hr-network?${qs}`);
      data = parsed.data;
      if (!parsed.ok) throw new Error(formatDetail(data?.detail) || `HTTP ${parsed.status}`);
    } catch (parseErr) {
      if (parseErr.loginRequired) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
        return;
      }
      throw parseErr;
    }
    currentCompany = data.company;
    lastGraph = data;
    lastAnalysis = data;
    currentCaseHit = await ensureCaseLookup(currentCompany);
    showBranchHintForCompany(currentCompany);
    renderSearchResults(data);
    // Erste Analyse = Ebene 2 (Firma + aktuelle/ehemalige) — Regler daran ausrichten.
    setDeepLevel(Number(data.level) || 2);
    rememberSearch(currentCompany);
    syncForceRefreshBtn();
    await transitionToResults();
    refreshCompanyCacheOffer();
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

async function runDeepAnalyze(level, maxPersonSearches, { forceRefresh = false } = {}) {
  selectedDeepLevel = level;
  hideNotify();
  const before = graphFingerprint(lastGraph);
  startDeepProgress(level);
  document.getElementById("deepBtn").disabled = true;
  try {
    const parsed = await fetchJson("/api/fraud-network/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        level,
        ad_hoc_company: {
          name: currentCompany.name || companyInput.value.trim(),
          uid: currentCompany.uid || pendingUid || "",
        },
        max_person_searches: maxPersonSearches,
        force_refresh: !!forceRefresh,
      }),
    });
    const data = parsed.data;
    if (!parsed.ok) throw new Error(formatDetail(data?.detail) || `HTTP ${parsed.status}`);
    lastGraph = data;
    setDeepLevel(Number(data.level) || level);
    paintNetworkView();
    renderPersonsTable(data.persons_table || [], currentCompany);
    const after = graphFingerprint(data);
    const added = {
      nodes: Math.max(0, after.nodes - before.nodes),
      edges: Math.max(0, after.edges - before.edges),
      persons: Math.max(0, after.persons - before.persons),
    };
    const ps = data.stats?.person_search || {};
    const shabBit = ps.searched ? ` · SHAB ${ps.matches || 0} in ${ps.elapsed_seconds || "?"}s` : "";
    if (data.cached) {
      stopDeepProgress();
      hideStatus();
      const when = formatDateTimeDisplay(data.cached_at);
      showNotify(
        `Aus Cache${when ? ` (${when})` : ""} — «Neu laden» für frische Registerdaten.`,
        { ok: true, sound: false }
      );
      showDeepCacheBar(level, maxPersonSearches, { fromCache: true, cachedAt: data.cached_at });
    } else {
      await finishDeepProgress();
      if (level >= 4) {
        showDeepCacheBar(level, maxPersonSearches, { fromCache: false });
      } else {
        hideDeepCacheBar();
      }
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
    }
  } catch (err) {
    stopDeepProgress();
    hideDeepCacheBar();
    if (err.loginRequired) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      return;
    }
    showError(err.message);
  } finally {
    document.getElementById("deepBtn").disabled = false;
  }
}

function showDeepCacheBar(level, maxPersonSearches, { fromCache = true, cachedAt = null } = {}) {
  const bar = document.getElementById("caDeepCacheBar");
  if (!bar) return;
  bar.classList.remove("hidden");
  bar.dataset.keep = fromCache || level >= 4 ? "1" : "";
  const when = formatDateTimeDisplay(cachedAt);
  const label = fromCache
    ? `Ebene ${level} aus Server-Cache (7 Tage)${when && when !== "—" ? ` · ${when}` : ""}.`
    : `Ebene ${level} für 7 Tage im Server-Cache gespeichert.`;
  bar.innerHTML = `
    <span>${label}</span>
    <button type="button" class="btn-nav" id="caDeepForceRefresh">Neu laden</button>
  `;
  document.getElementById("caDeepForceRefresh")?.addEventListener("click", () => {
    runDeepAnalyze(level, maxPersonSearches, { forceRefresh: true });
  });
}

function hideDeepCacheBar() {
  const bar = document.getElementById("caDeepCacheBar");
  if (!bar) return;
  bar.classList.add("hidden");
  bar.innerHTML = "";
  delete bar.dataset.keep;
}

async function refreshCompanyCacheOffer() {
  const bar = document.getElementById("caDeepCacheBar");
  if (!bar || !currentCompany) return;
  const name = currentCompany.name || "";
  const uid = currentCompany.uid || pendingUid || "";
  if (!name && !uid) return;
  try {
    const qs = new URLSearchParams();
    if (name) qs.set("name", name);
    if (uid) qs.set("uid", uid);
    const resp = await fetch(`/api/fraud-network/cache-status?${qs}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const levels = data.levels || {};
    const bits = [];
    for (const lvl of [5, 4]) {
      const info = levels[String(lvl)];
      if (info?.cached) {
        const when = formatDateTimeDisplay(info.cached_at);
        bits.push({
          level: lvl,
          when,
          nodes: info.nodes || 0,
        });
      }
    }
    if (!bits.length) {
      // Don't clear a "just stored" bar from a deep scan in this session
      if (!bar.dataset.keep) hideDeepCacheBar();
      return;
    }
    const best = bits[0];
    bar.classList.remove("hidden");
    bar.innerHTML = `
      <span>Cached Ebene ${best.level} verfügbar${best.when ? ` (${best.when})` : ""} — ${best.nodes} Knoten.</span>
      <span class="ca-deep-cache-actions">
        ${bits
          .map(
            (b) =>
              `<button type="button" class="btn-nav ca-cache-load" data-level="${b.level}">E${b.level} laden</button>`
          )
          .join("")}
      </span>
    `;
    bar.querySelectorAll(".ca-cache-load").forEach((btn) => {
      btn.addEventListener("click", () => {
        const lvl = Number(btn.dataset.level) || 4;
        setDeepLevel(lvl);
        runDeepAnalyze(lvl, FULL_PERSON_SEARCHES, { forceRefresh: false });
      });
    });
  } catch (_) {
    /* ignore */
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
  let el = document.getElementById("caNotify");
  if (!el) {
    el = document.createElement("div");
    el.id = "caNotify";
    el.className = "ca-toast hidden";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
  }
  // Viewport-fixed toasts must live under body (avoid is-idle position / overflow traps)
  if (el.parentElement !== document.body) {
    document.body.appendChild(el);
  }
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
  // Network: Graph (default) or Organigramm — preference in localStorage
  paintNetworkView();
  updateHeavyCompanyHint();
}

function renderFirmBar(company, data) {
  const card = document.getElementById("companyCard");
  const canton = company.canton || "";
  const status = String(company.status || "").toUpperCase();
  const statusClass = statusTone(status);
  let statusText = statusDisplayLabel(status);
  if (statusClass === "bad" && company.deletion_date) {
    statusText = `Gelöscht · ${formatDateDisplay(company.deletion_date)}`;
  }
  const seat = [cantonDisplayName(canton) || canton, company.legal_seat].filter(Boolean).join(" · ");
  const formShort = shortenLegalForm(company.legal_form);
  const onCase = !!currentCaseHit;
  const caseStatus = currentCaseHit?.status || "";
  const hr = safeHttpUrl(company.cantonal_excerpt_url);
  const metaCount = [company.uid, seat, formShort, data.publication_count != null].filter(Boolean).length;

  card.classList.toggle("is-on-fraudlist", onCase && caseStatus !== "under_review");
  const firmNameRaw = String(company.name || "").trim();
  const firmNameShow =
    typeof anon === "function" ? anon(firmNameRaw, "company") : firmNameRaw;
  const uidRaw = String(company.uid || "").trim();
  const uidShow = typeof anon === "function" ? anon(uidRaw, "uid") : uidRaw;

  card.innerHTML = `
    <div class="ca-firm-top">
      <div class="ca-firm-identity">
        <div class="ca-firm-name-row">
          <h2 class="ca-firm-name">${escHtml(firmNameShow)}</h2>
          ${copyBtnHtml(firmNameRaw, "Firmenname kopieren")}
        </div>
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
      ${uidRaw ? `<span class="ca-meta ca-meta-uid" title="UID">
        <span class="ca-meta-label">UID</span>
        <button type="button" class="ca-copy-value" data-copy="${escHtml(uidRaw)}" title="UID kopieren" aria-label="UID kopieren">
          <strong>${escHtml(uidShow)}</strong>
          ${COPY_ICON_SVG}
        </button>
      </span>` : ""}
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
  wireCopyButtons(card);
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
    return `<span class="ca-warn-pill${fraud ? " is-fraudlist" : ""}">${escHtml(formatDatesInText(w))}</span>`;
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
    box.innerHTML = `<p class="hr-empty">Keine Personen erkannt.<br>
      <span class="ca-muted-note">Personen kommen aus SHAB-Meldungen (Zefix <code>sogcPub</code>).
      Ist diese Liste bei Zefix leer, gibt es hier keinen Hit — bitte kantonalen HR-Auszug prüfen.</span></p>`;
    return;
  }
  const current = list.filter((p) => p.status !== "former");
  const former = list.filter((p) => p.status === "former");

  const renderPersonItem = (p) => {
    const name = p.name || "";
    const copyName = personNameVornameNachname(name);
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
          ${copyBtnHtml(copyName, `Name kopieren (${copyName || "—"})`)}
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
    html += `<details class="ca-person-group ca-person-group-former">
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
  wireCopyButtons(box);
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

function labelForMutationKey(key) {
  const k = String(key || "");
  if (!k) return "";
  if (MUTATION_LABELS[k]) return MUTATION_LABELS[k];
  // Prefix match like backend _label_for_key
  for (const part of Object.keys(MUTATION_LABELS)) {
    if (k === part || k.startsWith(`${part}.`) || k.startsWith(part)) {
      return MUTATION_LABELS[part];
    }
  }
  return k.replace(/[._]/g, " ").replace(/\s+/g, " ").trim();
}

function mutationLabels(pub) {
  if (pub.types_de && pub.types_de.length) return pub.types_de;
  return (pub.types || []).map((k) => labelForMutationKey(k) || k.replace(/\./g, " "));
}

/** Classify a Zefix mutation key or German label into a visual kind. */
function mutationKindFromKeyOrLabel(key, label) {
  const k = String(key || "").toLowerCase().trim();
  const l = String(label || "").toLowerCase().trim();
  const blob = `${k} ${l}`.trim();

  // Prefer exact / prefix key match (official Zefix mutationTypes)
  if (k === "status.neu" || k.startsWith("status.neu.")) return MUTATION_KIND.birth;
  if (k === "status.geloescht" || k.startsWith("status.geloescht")) return MUTATION_KIND.delete;
  if (k === "liquidation" || k.startsWith("liquidation") || k.includes("konkurs") || k.includes("nachlass")) {
    return MUTATION_KIND.liquid;
  }
  if (k === "aenderungorgane" || k.startsWith("aenderungorgane") || k.includes("organe")) {
    return MUTATION_KIND.organ;
  }
  if (k === "adresse" || k.startsWith("adresse") || k === "sitzverlegung" || k.startsWith("sitz")) {
    return MUTATION_KIND.address;
  }
  if (k === "zweck" || k.startsWith("zweck")) return MUTATION_KIND.purpose;
  if (k === "kapitalaenderung" || k.startsWith("kapital") || k.includes("kapital")) {
    return MUTATION_KIND.capital;
  }
  if (k === "statuten" || k.startsWith("statuten")) return MUTATION_KIND.statutes;
  if (k === "firmenname" || k.startsWith("firmenname") || k.includes("namens")) {
    return MUTATION_KIND.name;
  }
  if (
    k === "fusion" ||
    k.startsWith("fusion") ||
    k === "vermoegenstransfer" ||
    k.includes("vermoegen") ||
    k === "umwandlung" ||
    k.startsWith("umwandlung") ||
    k === "rechtsform" ||
    k.startsWith("rechtsform") ||
    k.includes("spaltung") ||
    k.includes("uebernahme") ||
    k.includes("übernahme")
  ) {
    return MUTATION_KIND.structure;
  }
  if (k === "status" || (k.startsWith("status") && !k.includes("neu") && !k.includes("geloescht"))) {
    return MUTATION_KIND.status;
  }

  // Label / free-text heuristics (types_de only, or message fallback)
  if (/(^|[\s·-])neu(eintragung)?(\s|$)|gründung|neugründung/.test(blob)) {
    return MUTATION_KIND.birth;
  }
  if (/geloescht|gelöscht|loeschung|löschung/.test(blob)) return MUTATION_KIND.delete;
  if (/liquidation|konkurs|nachlass/.test(blob)) return MUTATION_KIND.liquid;
  if (/organänderung|organaenderung|organe|ausgeschieden|eingetragen/.test(blob)) {
    return MUTATION_KIND.organ;
  }
  if (/adress|sitzverleg|domizil|\bsitz\b/.test(blob)) return MUTATION_KIND.address;
  if (/zweck/.test(blob)) return MUTATION_KIND.purpose;
  if (/kapital|kapitalerhöhung|kapitalherabsetzung/.test(blob)) return MUTATION_KIND.capital;
  if (/statuten/.test(blob)) return MUTATION_KIND.statutes;
  if (/firmenname|namensänderung|namensaenderung/.test(blob)) return MUTATION_KIND.name;
  if (/fusion|vermögen|vermoegen|übernahme|uebernahme|umwandlung|spaltung|rechtsform/.test(blob)) {
    return MUTATION_KIND.structure;
  }
  if (/status/.test(blob)) return MUTATION_KIND.status;
  return MUTATION_KIND.unknown;
}

/**
 * Structured type entries for one publication: key, DE label, visual kind.
 * Multi-type events yield multiple pills.
 */
function mutationTypeEntries(pub) {
  const keys = Array.isArray(pub.types) ? pub.types.filter(Boolean) : [];
  const labelsDe = Array.isArray(pub.types_de) ? pub.types_de.filter(Boolean) : [];
  const entries = [];
  const seen = new Set();

  if (keys.length) {
    keys.forEach((key, i) => {
      const label = (labelsDe[i] && String(labelsDe[i]).trim()) || labelForMutationKey(key) || String(key);
      const kind = mutationKindFromKeyOrLabel(key, label);
      const dedupe = `${kind}|${label.toLowerCase()}`;
      if (seen.has(dedupe)) return;
      seen.add(dedupe);
      entries.push({ key, label, kind });
    });
  } else if (labelsDe.length) {
    labelsDe.forEach((label) => {
      const kind = mutationKindFromKeyOrLabel("", label);
      const dedupe = `${kind}|${String(label).toLowerCase()}`;
      if (seen.has(dedupe)) return;
      seen.add(dedupe);
      entries.push({ key: "", label, kind });
    });
  }

  if (!entries.length) {
    // Heuristic from cleaned SHAB excerpt when Zefix sent no mutationTypes
    const msg = shabPublicationMessage(pub);
    const heuristic = [
      [/ausgeschieden|eingetragene personen|aenderungorgane/i, MUTATION_KIND.organ, "Organänderung"],
      [/sitzverlegung|adresse|domizil/i, MUTATION_KIND.address, "Adressänderung"],
      [/zweckänderung|zweck:/i, MUTATION_KIND.purpose, "Zweckänderung"],
      [/kapital/i, MUTATION_KIND.capital, "Kapitaländerung"],
      [/statuten/i, MUTATION_KIND.statutes, "Statutenänderung"],
      [/liquidation/i, MUTATION_KIND.liquid, "Liquidation"],
      [/gelöscht|löschung/i, MUTATION_KIND.delete, "Löschung"],
    ];
    for (const [re, kind, label] of heuristic) {
      if (re.test(msg)) {
        entries.push({ key: "", label, kind });
        break;
      }
    }
  }

  if (!entries.length) {
    entries.push({ key: "", label: "SHAB-Meldung", kind: MUTATION_KIND.unknown });
  }
  return entries;
}

function primaryTimelineTone(entries, hasPeople) {
  const kinds = entries.map((e) => e.kind);
  if (hasPeople && !kinds.includes(MUTATION_KIND.organ)) {
    kinds.push(MUTATION_KIND.organ);
  }
  for (const kind of MUTATION_KIND_PRIORITY) {
    if (kinds.includes(kind)) return kind;
  }
  return MUTATION_KIND.unknown;
}

function timelineTypePills(entries) {
  if (!entries.length) return "";
  return `<div class="ca-tl-types" role="list">
    ${entries
      .map(
        (e) =>
          `<span class="ca-tl-type ca-tl-type--${escHtml(e.kind)}" role="listitem">${escHtml(e.label)}</span>`
      )
      .join("")}
  </div>`;
}

/** Person chips: red = exited, green = entered (structured, no SHAB wall of text). */
function timelinePersonChips(list, kind) {
  if (!Array.isArray(list) || !list.length) return "";
  const label = kind === "out" ? "Ausgeschieden" : "Eingetragen";
  const chips = list.map((p) => {
    const name = String((p && p.name) || p || "").trim();
    if (!name) return "";
    const roles = Array.isArray(p?.roles)
      ? p.roles.filter(Boolean).slice(0, 2).join(", ")
      : "";
    return `<span class="ca-tl-chip ca-tl-chip--${kind}">
      <span class="ca-tl-chip-name">${escHtml(name)}</span>
      ${roles ? `<span class="ca-tl-chip-role">${escHtml(roles)}</span>` : ""}
    </span>`;
  }).filter(Boolean).join("");
  if (!chips) return "";
  return `<div class="ca-tl-people ca-tl-people--${kind}">
    <span class="ca-tl-people-label">${label}</span>
    <div class="ca-tl-chip-row">${chips}</div>
  </div>`;
}

/** Full API text for Details (prefer untruncated message_full). */
function shabPublicationMessage(pub) {
  return String(pub?.message_full || pub?.message_short || "").trim();
}

/** Soft paragraph breaks before known SHAB section markers; preserve umlauts. */
function formatShabProse(raw) {
  let text = repairMojibake(decodeBasicEntities(raw || ""));
  text = formatDatesInText(text).replace(/[ \t\f\v]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  if (!text) return "";
  // Prefer word-boundary section breaks without lookbehind (broader browser support)
  text = text.replace(
    /(\S)\s+(?=(?:Statutenänderung|Zweckänderung|Kapitaländerung|Kapitalerhöhung|Kapitalherabsetzung|Namensänderung|Firmenname|Sitz\s+neu|Zweck\s+neu|Domizil\s+neu|Neue\s+Adresse|Adresse\s+neu|Firma\s+neu|Publizierte\s+Statuten|Ausgeschiedene\s+Personen|Eingetragene\s+Personen)\b)/gi,
    "$1\n\n"
  );
  return text.replace(/\n{3,}/g, "\n\n").trim();
}

/**
 * Full SHAB body under Details. Long prose may start collapsed mid-word-safe with «Mehr anzeigen».
 */
function timelineMessageDetailsHtml(rawMsg) {
  const full = formatShabProse(rawMsg);
  if (!full) return "";
  const PREVIEW = 480;
  if (full.length <= PREVIEW) {
    return `<div class="ca-timeline-msg hidden">${escHtml(full)}</div>`;
  }
  let cut = full.slice(0, PREVIEW);
  const sp = Math.max(cut.lastIndexOf(" "), cut.lastIndexOf("\n"));
  if (sp > PREVIEW >> 1) cut = cut.slice(0, sp);
  cut = cut.replace(/[ \t,.;:]+$/u, "");
  const rest = full.slice(cut.length);
  return `<div class="ca-timeline-msg hidden" data-shab-expandable="1">
    <span class="ca-tl-msg-lead">${escHtml(cut)}</span><span class="ca-tl-msg-ellipsis">…</span><span class="ca-tl-msg-rest hidden">${escHtml(rest)}</span>
    <button type="button" class="ca-tl-msg-more">Mehr anzeigen</button>
  </div>`;
}

function wireTimelineMessageExpand(root) {
  root.querySelectorAll(".ca-timeline-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const msg = btn.nextElementSibling;
      if (!msg || !msg.classList.contains("ca-timeline-msg")) return;
      const open = !msg.classList.contains("hidden");
      msg.classList.toggle("hidden", open);
      btn.textContent = open ? "Details" : "Weniger";
    });
  });
  root.querySelectorAll(".ca-tl-msg-more").forEach((btn) => {
    btn.addEventListener("click", () => {
      const box = btn.closest(".ca-timeline-msg");
      if (!box) return;
      const rest = box.querySelector(".ca-tl-msg-rest");
      const dots = box.querySelector(".ca-tl-msg-ellipsis");
      const expanded = rest && !rest.classList.contains("hidden");
      if (rest) rest.classList.toggle("hidden", expanded);
      if (dots) dots.classList.toggle("hidden", !expanded);
      btn.textContent = expanded ? "Mehr anzeigen" : "Weniger anzeigen";
    });
  });
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
    const entries = mutationTypeEntries(pub);
    const personsIn = Array.isArray(pub.persons_in) ? pub.persons_in : [];
    const personsOut = Array.isArray(pub.persons_out) ? pub.persons_out : [];
    const hasPeople = personsIn.length > 0 || personsOut.length > 0;
    // Person chips imply an organ event even if type list omitted it
    if (hasPeople && !entries.some((e) => e.kind === MUTATION_KIND.organ)) {
      const onlyGeneric =
        entries.length === 1 && entries[0].kind === MUTATION_KIND.unknown;
      if (onlyGeneric) {
        entries[0] = { key: "aenderungorgane", label: "Organänderung", kind: MUTATION_KIND.organ };
      } else {
        entries.unshift({ key: "aenderungorgane", label: "Organänderung", kind: MUTATION_KIND.organ });
      }
    }
    const tone = primaryTimelineTone(entries, hasPeople);
    const rawDate = pub.date || "";
    const dateCh = formatDateCH(rawDate);
    const year = String(rawDate).slice(0, 4);
    if (year && year !== lastYear && /^\d{4}$/.test(year)) {
      html += `<li class="ca-timeline-year-break" aria-hidden="true"><span>${escHtml(year)}</span></li>`;
      lastYear = year;
    }
    const peopleHtml =
      timelinePersonChips(personsOut, "out") + timelinePersonChips(personsIn, "in");
    const fullMsg = shabPublicationMessage(pub);
    // Full SHAB prose when no person chips (chips already surface the organ change)
    const showMsgToggle = Boolean(fullMsg) && !hasPeople;
    const titleAria = entries.map((e) => e.label).join(" · ");
    html += `<li class="ca-timeline-item ca-tone-${tone}">
      <div class="ca-timeline-rail" aria-hidden="true"></div>
      <div class="ca-timeline-card">
        <time class="ca-timeline-date" datetime="${escHtml(rawDate)}">${escHtml(dateCh)}</time>
        <div class="ca-timeline-title-row" aria-label="${escHtml(titleAria)}">
          ${timelineTypePills(entries)}
        </div>
        ${peopleHtml || ""}
        ${showMsgToggle
          ? `<button type="button" class="ca-timeline-toggle">Details</button>
             ${timelineMessageDetailsHtml(fullMsg)}`
          : ""}
      </div>
    </li>`;
  }
  html += `</ol>`;
  box.innerHTML = html;
  wireTimelineMessageExpand(box);
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
    const rawMsg = repairMojibake(decodeBasicEntities(shabPublicationMessage(pub)));
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

/** localStorage: "graph" (default) | "board" Organigramm */
const NETWORK_VIEW_KEY = "lynx_ca_network_view";

function getNetworkViewMode() {
  try {
    const v = localStorage.getItem(NETWORK_VIEW_KEY);
    if (v === "board" || v === "graph") return v;
  } catch (_) { /* ignore */ }
  return "graph";
}

function setNetworkViewMode(mode) {
  const m = mode === "board" ? "board" : "graph";
  try {
    localStorage.setItem(NETWORK_VIEW_KEY, m);
  } catch (_) { /* ignore */ }
  return m;
}

function wireNetworkViewToggle() {
  document.querySelectorAll(".ca-view-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = setNetworkViewMode(btn.dataset.view || "graph");
      syncNetworkViewToggleUi(mode);
      paintNetworkView();
    });
  });
  syncNetworkViewToggleUi(getNetworkViewMode());
}

function syncNetworkViewToggleUi(mode) {
  document.querySelectorAll(".ca-view-toggle-btn").forEach((btn) => {
    const on = btn.dataset.view === mode;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const panel = document.getElementById("caGraphPanel");
  panel?.classList.toggle("is-board-view", mode === "board");
  panel?.classList.toggle("is-graph-view", mode !== "board");
  document.getElementById("caGraph")?.classList.toggle("hidden", mode === "board");
  document.getElementById("caOrgBoard")?.classList.toggle("hidden", mode !== "board");
  document.getElementById("caGraphLegend")?.classList.toggle("hidden", mode === "board");
  document.getElementById("caOrgLegend")?.classList.toggle("hidden", mode !== "board");
  document.querySelectorAll(".ca-graph-only-ctrl").forEach((el) => {
    el.classList.toggle("hidden", mode === "board");
  });
  const find = document.getElementById("caGraphFindInput");
  if (find) {
    find.placeholder = mode === "board" ? "Person / Firma finden…" : "Knoten suchen…";
  }
}

function destroyVisNetwork() {
  if (networkInstance) {
    try {
      networkInstance.destroy();
    } catch (_) { /* ignore */ }
    networkInstance = null;
  }
  const canvas = document.getElementById("caGraph");
  if (canvas) canvas.innerHTML = "";
}

/** Paint Graph or Organigramm from lastGraph / lastAnalysis. */
function paintNetworkView() {
  const nodes = lastGraph?.nodes || lastAnalysis?.nodes || [];
  const edges = lastGraph?.edges || lastAnalysis?.edges || [];
  const mode = getNetworkViewMode();
  syncNetworkViewToggleUi(mode);
  if (!nodes.length) {
    destroyVisNetwork();
    const board = document.getElementById("caOrgBoard");
    const canvas = document.getElementById("caGraph");
    if (mode === "board" && board) {
      board.innerHTML = `<p class="hr-empty">Keine Netzwerk-Daten.</p>`;
    } else if (canvas) {
      canvas.innerHTML = `<p class="hr-empty">Keine Graph-Daten.</p>`;
    }
    return;
  }
  if (mode === "board") {
    destroyVisNetwork();
    renderOrgBoard(nodes, edges, currentCompany || lastAnalysis?.company || {});
  } else {
    const board = document.getElementById("caOrgBoard");
    if (board) board.innerHTML = "";
    renderGraph(nodes, edges, "caGraph", (n) => {
      networkInstance = n;
    });
  }
  const findVal = document.getElementById("caGraphFindInput")?.value;
  if (findVal) findGraphNodes(findVal);
}

function roleRank(roles) {
  const j = (roles || []).join(" ").toLowerCase();
  if (/präsident|vorsitz/.test(j)) return 1;
  if (/geschäftsführer/.test(j)) return 2;
  if (/verwaltungsrat/.test(j)) return 3;
  if (/prokurist|zeichnungs/.test(j)) return 4;
  if (/gesellschafter|inhaber/.test(j)) return 5;
  if (/liquidator/.test(j)) return 6;
  return 9;
}

function formatPersonBoardName(raw) {
  const ordered = personNameVornameNachname(raw);
  return typeof anon === "function" ? anon(ordered, "person") : ordered;
}

/**
 * HR-Style «Nachname, Vorname» → Clipboard «Vorname Nachname» (keine Kommas).
 */
function personNameVornameNachname(name) {
  const raw = String(name || "").replace(/\s+/g, " ").trim();
  if (!raw) return "";
  if (!raw.includes(",")) return raw;
  const idx = raw.indexOf(",");
  const last = raw.slice(0, idx).trim();
  const first = raw.slice(idx + 1).trim();
  return [first, last].filter(Boolean).join(" ");
}

const COPY_ICON_SVG = `<svg class="ca-copy-svg" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
const COPY_CHECK_SVG = `<svg class="ca-copy-svg" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>`;

/** Small icon button; `text` is raw clipboard payload (never anonymized). */
function copyBtnHtml(text, title = "Kopieren") {
  const t = String(text || "").trim();
  if (!t) return "";
  return `<button type="button" class="ca-copy-btn" data-copy="${escHtml(t)}" title="${escHtml(title)}" aria-label="${escHtml(title)}">${COPY_ICON_SVG}</button>`;
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
  // Bottom toast as secondary signal (always on document body context)
  if (typeof showNotify === "function") {
    showNotify(`In Zwischenablage: ${short}`, { ok: true, duration: 2000 });
  }
}

async function copyTextToClipboard(text, btn) {
  const t = String(text || "").trim();
  if (!t) return false;
  try {
    // Prefer Clipboard API when available (secure context)
    if (navigator.clipboard?.writeText && window.isSecureContext !== false) {
      await navigator.clipboard.writeText(t);
    } else {
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      ta.style.top = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, t.length);
      const ok = document.execCommand("copy");
      ta.remove();
      if (!ok) throw new Error("execCommand failed");
    }
    flashCopySuccess(btn, t);
    return true;
  } catch (_) {
    // Fallback once more with execCommand if Clipboard API failed
    try {
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      if (!ok) throw new Error("execCommand failed");
      flashCopySuccess(btn, t);
      return true;
    } catch (__) {
      if (typeof showNotify === "function") {
        showNotify("Kopieren fehlgeschlagen — bitte manuell markieren.", { ok: false, duration: 3200 });
      } else {
        showCopyBubble(btn, "Kopieren fehlgeschlagen");
      }
      return false;
    }
  }
}

function wireCopyButtons(root) {
  if (!root) return;
  root.querySelectorAll("[data-copy]").forEach((btn) => {
    if (btn.dataset.copyWired === "1") return;
    btn.dataset.copyWired = "1";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      // dataset prefers decoded payload; getAttribute is fallback
      const payload = (btn.dataset.copy != null ? btn.dataset.copy : btn.getAttribute("data-copy")) || "";
      copyTextToClipboard(payload, btn);
    });
  });
}

function personRolesForBoard(n, rolesByNode) {
  // Prefer roles on edges to seed (board scopes to seed); node.roles can merge multi-firm L5 data
  const fromEdges = rolesByNode?.get?.(n.id) ? [...rolesByNode.get(n.id)] : [];
  if (fromEdges.length) return dedupeRoleLabels(fromEdges);
  const raw = (n.roles && n.roles.length) ? n.roles : [];
  return dedupeRoleLabels(raw);
}

function signingHintsFromRoles(roles) {
  const j = (roles || []).join(" ").toLowerCase();
  const out = [];
  if (/einzelunterschrift|einzeln zeichn/.test(j)) out.push("Einzelunterschrift");
  if (/kollektiv.*zwei|zu zweien|kollektivunterschrift/.test(j)) out.push("Kollektivunterschrift");
  return out;
}

/**
 * Variante A: Organigramm nur für die Kernfirma.
 * Personen/Firmen mit Kante zur Seed — L3–5-Ring gehört in den Graph.
 */
function renderOrgBoard(nodes, edges, company) {
  const board = document.getElementById("caOrgBoard");
  if (!board) return;
  const list = nodes || [];
  if (!list.length) {
    board.innerHTML = `<p class="hr-empty">Keine Daten für Organigramm.</p>`;
    return;
  }

  const seed =
    list.find((n) => n.is_seed) ||
    list.find((n) => n.type === "company" && n.is_center) ||
    list.find((n) => n.type === "company");
  const seedId = seed?.id;
  if (!seedId) {
    board.innerHTML = `<p class="hr-empty">Keine Kernfirma im Netzwerk.</p>`;
    return;
  }

  // Nur direkte Nachbarn der Kernfirma (Kante berührt seed)
  const linkedIds = new Set();
  const rolesByNode = new Map(); // personId -> Set of role labels from edges to seed
  for (const e of edges || []) {
    const a = e.from;
    const b = e.to;
    if (a !== seedId && b !== seedId) continue;
    const other = a === seedId ? b : a;
    if (other == null) continue;
    linkedIds.add(other);
    const lab = String(e.label || "").trim();
    if (!lab) continue;
    if (!rolesByNode.has(other)) rolesByNode.set(other, new Set());
    rolesByNode.get(other).add(lab);
  }

  const byId = new Map(list.map((n) => [n.id, n]));
  const persons = [...linkedIds]
    .map((id) => byId.get(id))
    .filter((n) => n && n.type === "person");

  const current = persons
    .filter((n) => n.person_status !== "former")
    .sort((a, b) => roleRank(a.roles) - roleRank(b.roles) || String(a.label || "").localeCompare(String(b.label || "")));
  const former = persons
    .filter((n) => n.person_status === "former")
    .sort((a, b) => String(b.exited_date || b.last_seen || "").localeCompare(String(a.exited_date || a.last_seen || "")));

  // Nur Firmen mit direkter Kante zur Seed (Zefix-Struktur), nicht L3+ Mandate-Netz
  const relatedCos = [...linkedIds]
    .map((id) => byId.get(id))
    .filter((n) => n && n.type === "company" && n.id !== seedId);

  const firmNameRaw = seed?.label || seed?.name || company?.name || "Firma";
  const firmName =
    typeof anon === "function" ? anon(String(firmNameRaw).split("\n")[0], "company") : String(firmNameRaw).split("\n")[0];
  const firmUid = seed?.uid || company?.uid || "";
  const firmStatus = seed?.status || company?.status || "";
  const firmCanton = seed?.canton || company?.canton || "";
  const firmMeta = [firmUid, firmCanton, firmStatus].filter(Boolean).join(" · ");

  const personCard = (n, kind) => {
    const roles = personRolesForBoard(n, rolesByNode);
    const rawName = n.label || n.name || "";
    const copyName = personNameVornameNachname(rawName);
    const name = formatPersonBoardName(rawName);
    const caseHit = !!(n.case_involved || n.on_watchlist);
    const sign = signingHintsFromRoles(roles);
    const period =
      kind === "former"
        ? [n.first_seen && `ab ${formatDateCH(n.first_seen)}`, n.exited_date && `bis ${formatDateCH(n.exited_date)}`]
            .filter(Boolean)
            .join(" · ") ||
          (n.last_seen ? `bis ${formatDateCH(n.last_seen)}` : "")
        : n.first_seen
          ? `seit ${formatDateCH(n.first_seen)}`
          : "";
    const roleHtml = roles.length
      ? `<ul class="ca-org-roles">${roles.map((r) => `<li>${escHtml(r)}</li>`).join("")}</ul>`
      : `<p class="ca-org-roles-empty">Keine Rolle im SHAB-Text</p>`;
    const findText = [name, roles.join(" "), kind, n.residence || ""].join(" ").toLowerCase();
    return `<article class="ca-org-person ca-org-person--${kind}${caseHit ? " is-case" : ""}" data-find-text="${escHtml(findText)}" data-node-id="${escHtml(n.id || "")}">
      <header class="ca-org-person-head">
        <span class="ca-org-status-pill ca-org-status-pill--${kind}">${kind === "former" ? "Ehemalig" : "Aktuell"}</span>
        ${caseHit ? `<span class="ca-org-case-pill" title="Fall / Watchlist">Fall</span>` : ""}
      </header>
      <h4 class="ca-org-person-name">${escHtml(name)}${copyBtnHtml(copyName, `Name kopieren (${copyName || "—"})`)}</h4>
      ${roleHtml}
      ${sign.length ? `<p class="ca-org-sign">${escHtml(sign.join(" · "))}</p>` : ""}
      ${period ? `<p class="ca-org-period">${escHtml(period)}</p>` : ""}
      ${n.residence ? `<p class="ca-org-meta">${escHtml(typeof anon === "function" ? anon(n.residence, "place") : n.residence)}</p>` : ""}
    </article>`;
  };

  const companyChip = (n) => {
    const nameRaw = String(n.label || n.name || "").split("\n")[0];
    const name = typeof anon === "function" ? anon(nameRaw, "company") : nameRaw;
    const findText = [name, n.uid || "", n.role_hint || ""].join(" ").toLowerCase();
    const qs = new URLSearchParams();
    if (nameRaw) qs.set("company", nameRaw);
    if (n.uid) qs.set("uid", String(n.uid));
    return `<a class="ca-org-related-card" data-find-text="${escHtml(findText)}" href="/?${qs.toString()}" target="_blank" rel="noopener">
      <strong>${escHtml(name)}</strong>
      <span>${escHtml([n.uid, n.role_hint || n.status].filter(Boolean).join(" · ") || "verbunden")}</span>
    </a>`;
  };

  const networkPersonCount = list.filter((n) => n.type === "person").length;
  const networkCompanyCount = list.filter((n) => n.type === "company" && n.id !== seedId).length;
  const scopedOut =
    networkPersonCount > persons.length || networkCompanyCount > relatedCos.length;
  const levelNote = scopedOut
    ? `<p class="ca-org-level-note">Organigramm zeigt nur direkte Bindungen zur Kernfirma
        (${persons.length} Personen${relatedCos.length ? `, ${relatedCos.length} Struktur-Firmen` : ""}).
        Graph-Ebene ${escHtml(String(lastGraph?.level || lastAnalysis?.level || selectedDeepLevel || "—"))}:
        ${networkPersonCount} Personen · ${networkCompanyCount + 1} Firmen im Netzwerk.</p>`
    : "";

  board.innerHTML = `
    <div class="ca-org-layout">
      <header class="ca-org-firm" data-find-text="${escHtml((firmName + " " + firmMeta).toLowerCase())}">
        <span class="ca-org-firm-kicker">Kernfirma</span>
        <h3 class="ca-org-firm-name">${escHtml(firmName)}${copyBtnHtml(String(firmNameRaw).split("\n")[0].trim(), "Firmenname kopieren")}</h3>
        ${firmUid ? `<p class="ca-org-firm-meta"><button type="button" class="ca-copy-value ca-org-uid-copy" data-copy="${escHtml(firmUid)}" title="UID kopieren" aria-label="UID kopieren"><span>${escHtml(firmUid)}</span>${COPY_ICON_SVG}</button>${firmCanton || firmStatus ? ` · ${escHtml([firmCanton, firmStatus].filter(Boolean).join(" · "))}` : ""}</p>` : (firmMeta ? `<p class="ca-org-firm-meta">${escHtml(firmMeta)}</p>` : "")}
      </header>
      ${levelNote}
      <div class="ca-org-split">
        <section class="ca-org-col ca-org-col--current" aria-label="Aktuelle Personen">
          <h4 class="ca-org-col-title">
            Aktuell im Register
            <span class="ca-org-count">${current.length}</span>
          </h4>
          ${
            current.length
              ? `<div class="ca-org-cards">${current.map((p) => personCard(p, "current")).join("")}</div>`
              : `<p class="hr-empty ca-org-empty">Keine aktuellen Organe aus SHAB erkannt.</p>`
          }
        </section>
        <section class="ca-org-col ca-org-col--former" aria-label="Ehemalige Personen">
          <h4 class="ca-org-col-title">
            Ehemalig
            <span class="ca-org-count">${former.length}</span>
          </h4>
          ${
            former.length
              ? `<div class="ca-org-cards">${former.map((p) => personCard(p, "former")).join("")}</div>`
              : `<p class="hr-empty ca-org-empty">Keine ehemaligen Organe in den verfügbaren Meldungen.</p>`
          }
        </section>
      </div>
      ${
        relatedCos.length
          ? `<section class="ca-org-related" aria-label="Struktur-Firmen der Kernfirma">
              <h4 class="ca-org-col-title">Struktur (Zefix, direkt) <span class="ca-org-count">${relatedCos.length}</span></h4>
              <div class="ca-org-related-grid">${relatedCos.map(companyChip).join("")}</div>
            </section>`
          : ""
      }
    </div>
  `;
  wireCopyButtons(board);
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
      opacity: caseInvolved ? 1 : (isFormer ? 0.58 : 1),
    };
    if (!isPerson) {
      return { ...base, shape: "box", margin: 10, cursor: "pointer" };
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
        opacity: touchesFormer ? 0.45 : 0.9,
      },
      width: touchesFormer ? 1.15 : 2.4,
      selectionWidth: touchesFormer ? 1.6 : 3.2,
      hoverWidth: touchesFormer ? 1.5 : 3,
      smooth: { type: "continuous", roundness: 0.35 },
    };
  }));

  // Ultrawide / high-DPR (e.g. ThinkVision P40): fewer physics ticks, no shadows, drag-lite
  const heavy = nodes.length > 30 || edges.length > 45;
  const dpr = Number(window.devicePixelRatio) || 1;
  const hiDpi = dpr >= 1.5 || window.innerWidth >= 2560;
  const useShadows = !heavy && !hiDpi;
  const physIters = heavy ? 55 : (hiDpi ? 75 : 110);

  const net = new vis.Network(container, { nodes: visNodes, edges: visEdges }, {
    autoResize: true,
    height: "100%",
    width: "100%",
    nodes: {
      shadow: useShadows
        ? { enabled: true, color: "rgba(0,0,0,0.35)", size: 8, x: 0, y: 2 }
        : false,
      font: {
        // Canvas text is sharper with system stack on Windows high-DPI
        face: hiDpi ? "system-ui,Segoe UI,sans-serif" : "Rajdhani,system-ui,sans-serif",
      },
    },
    edges: {
      chosen: true,
      font: { size: 0 },
      smooth: heavy || hiDpi
        ? { type: "continuous", roundness: 0.2 }
        : { type: "continuous", roundness: 0.35 },
    },
    physics: {
      enabled: true,
      adaptiveTimestep: true,
      barnesHut: {
        gravitationalConstant: heavy ? -9000 : -12000,
        springLength: heavy ? 130 : 155,
        springConstant: 0.04,
        avoidOverlap: heavy ? 0.25 : 0.4,
        damping: 0.45,
      },
      stabilization: {
        enabled: true,
        iterations: physIters,
        updateInterval: heavy || hiDpi ? 40 : 25,
        fit: true,
      },
    },
    interaction: {
      hover: !heavy,
      tooltipDelay: heavy ? 120 : 80,
      hideEdgesOnDrag: heavy || hiDpi,
      hideEdgesOnZoom: heavy || hiDpi,
      zoomView: true,
      dragView: true,
    },
  });
  // Re-apply per-node fonts after global font option (global is defaults only)
  if (hiDpi) {
    try {
      const updates = nodes.map((n) => {
        const isPerson = n.type === "person";
        const isFormer = isPerson && n.person_status === "former";
        const caseInvolved = isPerson && !!(n.case_involved || n.on_watchlist);
        return {
          id: n.id,
          font: {
            color: caseInvolved ? "#fde68a" : (isFormer ? "#6b7280" : "#f8fafc"),
            face: "system-ui, Segoe UI, sans-serif",
            size: n.is_seed ? 14 : (isFormer ? 11 : 12),
            bold: !!(n.is_seed || caseInvolved),
            multi: true,
            vadjust: isPerson ? 2 : 0,
          },
        };
      });
      visNodes.update(updates);
    } catch (_) { /* ignore */ }
  }
  setInstance?.(net);
  const seedId = nodes.find((n) => n.is_seed)?.id || null;
  // Mark seed on vis node for later focus helpers
  if (seedId != null) {
    try { visNodes.update({ id: seedId, isSeed: true }); } catch (_) { /* ignore */ }
  }
  // Company nodes → open firm analysis in a new Lynx tab
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  net.on("click", (params) => {
    const nid = params?.nodes?.[0];
    if (nid == null) return;
    const n = nodeById.get(nid);
    if (!n || n.type === "person") return;
    const name = String(n.name || n.label || "").split("\n")[0].trim();
    if (!name) return;
    const qs = new URLSearchParams();
    qs.set("company", name);
    if (n.uid) qs.set("uid", String(n.uid));
    window.open(`/?${qs.toString()}`, "_blank", "noopener,noreferrer");
  });
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
  if (s.length <= max) return s;
  let cut = s.slice(0, Math.max(1, max - 1));
  const sp = cut.lastIndexOf(" ");
  if (sp > (max >> 1)) cut = cut.slice(0, sp);
  return `${cut.trimEnd()}…`;
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
