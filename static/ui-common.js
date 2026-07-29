/** Shared UI: expert mode, anonymize mode, site navigation. */

const EXPERT_MODE_KEY = "lynx_expert_mode";
const LEGACY_STORAGE_PREFIX = "fh_";

(function clearLegacyStorage() {
  try {
    const remove = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(LEGACY_STORAGE_PREFIX)) remove.push(k);
    }
    remove.forEach((k) => localStorage.removeItem(k));
  } catch (_) {}
})();

const VERDICT_DE = {
  "Likely Legitimate": "Wahrscheinlich seriös",
  "Use Caution": "Vorsicht geboten",
  "High Risk": "Hohes Risiko",
  "Likely Fraudulent": "Wahrscheinlich betrügerisch",
  "Critical Risk": "Kritisches Risiko",
};

/** Primary = everyday tools; more = overflow; compliance = primary only for compliance role. */
const SITE_NAV = [
  { href: "/", label: "Analyse", group: "primary", roles: ["case_manager", "admin", "compliance"] },
  { href: "/cases", label: "Fälle", group: "primary", roles: ["case_manager", "admin", "compliance"] },
  { href: "/watchlist", label: "Watchlist", group: "primary", roles: ["case_manager", "admin", "compliance"] },
  { href: "/compliance", label: "Compliance", group: "compliance", roles: ["compliance", "admin"] },
  { href: "/website-check", label: "Website-Check", group: "more", roles: ["case_manager", "admin", "compliance"] },
  { href: "/history", label: "Verlauf", group: "more", roles: ["case_manager", "admin", "compliance"] },
  { href: "/compare", label: "Vergleich", group: "more", roles: ["case_manager", "admin", "compliance"] },
  { href: "/blocklist", label: "Blocklist", group: "more", roles: ["case_manager", "admin", "compliance"] },
  { href: "/goldlist", label: "Goldlist", group: "more", roles: ["case_manager", "admin", "compliance"] },
  { href: "/admin", label: "Admin", group: "more", roles: ["admin"] },
  { href: "/profiler", label: "Profiler", group: "more", roles: ["admin"] },
  { href: "/profiler-cases", label: "Profiler-Fälle", group: "more", roles: ["admin"] },
];

const ROLE_LABEL = {
  admin: "Admin",
  case_manager: "Case Manager",
  compliance: "Compliance",
  analyst: "Case Manager", // legacy
};

window.__lynxUser = null;
window.__lynxSettings = { anonymize_mode: false };

function translateVerdict(verdict) {
  return VERDICT_DE[verdict] || verdict;
}

function isExpertMode() {
  return localStorage.getItem(EXPERT_MODE_KEY) === "1";
}

function setExpertMode(on) {
  localStorage.setItem(EXPERT_MODE_KEY, on ? "1" : "0");
  document.body.classList.toggle("expert-mode", on);
  const toggle = document.getElementById("expertModeToggle");
  if (toggle) toggle.checked = on;
  if (typeof window.onExpertModeChange === "function") {
    window.onExpertModeChange(on);
  }
}

function initExpertModeToggle() {
  document.body.classList.toggle("expert-mode", isExpertMode());
  const toggle = document.getElementById("expertModeToggle");
  if (!toggle) return;
  toggle.checked = isExpertMode();
  toggle.addEventListener("change", () => setExpertMode(toggle.checked));
}

/* ── Anonymize mode (server-backed, for demos / tests) ── */

function isAnonymizeMode() {
  return !!window.__lynxSettings?.anonymize_mode;
}

function ensureAnonBanner() {
  let el = document.getElementById("lynxAnonBanner");
  if (el) return el;
  el = document.createElement("div");
  el.id = "lynxAnonBanner";
  el.className = "lynx-anon-banner hidden";
  el.setAttribute("role", "status");
  el.innerHTML =
    "<strong>Anonymisierungsmodus</strong> — Namen, UIDs und Orte sind maskiert (Demo/Test). Umschalten im Admin-Panel.";
  document.body.prepend(el);
  return el;
}

function applyAnonymizeMode(on, { silent = false } = {}) {
  window.__lynxSettings = window.__lynxSettings || {};
  const prev = !!window.__lynxSettings.anonymize_mode;
  window.__lynxSettings.anonymize_mode = !!on;
  document.body.classList.toggle("anonymize-mode", !!on);
  const banner = ensureAnonBanner();
  banner.classList.toggle("hidden", !on);
  const toggle = document.getElementById("adminAnonToggle");
  if (toggle) toggle.checked = !!on;
  if (!silent && prev !== !!on && typeof window.onAnonymizeModeChange === "function") {
    window.onAnonymizeModeChange(!!on);
  }
}

/** Stable hash → same input always maps to the same demo label. */
function anonHash(s) {
  let h = 2166136261;
  const str = String(s ?? "");
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h >>> 0);
}

/**
 * Display-only anonymization. Raw data in memory stays intact for API calls.
 * @param {string|null|undefined} value
 * @param {"company"|"person"|"uid"|"place"|"address"|"iban"|"clearing"|"bank"|"text"|"user"|"name"} kind
 */
function anon(value, kind = "name") {
  if (!isAnonymizeMode() || value == null || value === "") return value ?? "";
  const raw = String(value);
  const h = anonHash(raw.toLowerCase().trim());
  const letter = String.fromCharCode(65 + (h % 26));
  switch (kind) {
    case "company":
      return `Demo-Firma ${letter}-${(h % 900) + 100}`;
    case "person":
    case "name":
      return `Demo-Person ${letter}.${(h % 90) + 10}`;
    case "uid":
      return `CHE-${String(100 + (h % 900)).padStart(3, "0")}.${String(h % 1000).padStart(3, "0")}.${String((h >> 3) % 1000).padStart(3, "0")}`;
    case "place":
      return `Ort-${h % 100}`;
    case "address":
      return `Adresse ${h % 200}`;
    case "iban":
    case "clearing":
      return `····${String(h % 10000).padStart(4, "0")}`;
    case "bank":
      return `Bank ${letter}`;
    case "user":
      return `Benutzer-${h % 100}`;
    case "text":
      return raw.length > 48 ? `[anonymisiert · ${raw.length} Z.]` : "[anonymisiert]";
    default:
      return `Demo-${h % 10000}`;
  }
}

function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function currentNavPath() {
  const path = location.pathname.replace(/\/$/, "") || "/";
  return path;
}

function isNavActive(href, path) {
  const h = href.replace(/\/$/, "") || "/";
  if (h === "/") return path === "/";
  return path === h || path.startsWith(`${h}/`);
}

async function loadCurrentUser() {
  try {
    const resp = await fetch("/api/me");
    if (!resp.ok) return null;
    const data = await resp.json();
    window.__lynxUser = data.user || null;
    applyAnonymizeMode(!!data.settings?.anonymize_mode, { silent: true });
    return window.__lynxUser;
  } catch (_) {
    return null;
  }
}

function closeNavDropdown(root) {
  const wrap = root || document.querySelector(".nav-dropdown");
  if (!wrap) return;
  const btn = wrap.querySelector(".nav-dropdown-trigger");
  const panel = wrap.querySelector(".nav-dropdown-panel");
  if (btn) btn.setAttribute("aria-expanded", "false");
  panel?.classList.add("hidden");
}

function closeAllNavDropdowns() {
  document.querySelectorAll(".nav-dropdown").forEach((wrap) => closeNavDropdown(wrap));
}

function wireNavDropdown(wrap) {
  const btn = wrap.querySelector(".nav-dropdown-trigger");
  const panel = wrap.querySelector(".nav-dropdown-panel");
  if (!btn || !panel || wrap.dataset.navWired === "1") return;
  wrap.dataset.navWired = "1";

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = btn.getAttribute("aria-expanded") === "true";
    closeAllNavDropdowns();
    if (!open) {
      btn.setAttribute("aria-expanded", "true");
      panel.classList.remove("hidden");
    }
  });

  panel.addEventListener("click", (e) => e.stopPropagation());
}

function ensureNavDocListeners() {
  if (window.__lynxNavDocWired) return;
  window.__lynxNavDocWired = true;
  document.addEventListener("click", () => closeAllNavDropdowns());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAllNavDropdowns();
  });
}

function canSeeNavItem(item, role) {
  if (!item.roles || !item.roles.length) return true;
  return item.roles.includes(role);
}

function renderSiteNav() {
  const nav = document.getElementById("siteNav");
  if (!nav) return;
  const path = currentNavPath();
  const role = window.__lynxUser?.role || "case_manager";
  const items = SITE_NAV.filter((i) => canSeeNavItem(i, role));

  const primary = items.filter((i) => i.group === "primary");
  if (role === "compliance") {
    primary.push(...items.filter((i) => i.group === "compliance"));
  }
  const more = items.filter(
    (i) => i.group === "more" || (i.group === "compliance" && role !== "compliance")
  );
  const moreActive = more.some((i) => isNavActive(i.href, path));

  const link = (item, extraClass = "") => {
    const active = isNavActive(item.href, path);
    const cls = ["nav-link", extraClass, active ? "nav-link-active" : ""].filter(Boolean).join(" ");
    return `<a href="${escHtml(item.href)}" class="${cls}" role="menuitem">${escHtml(item.label)}</a>`;
  };

  const moreDropdown = more.length
    ? `<span class="nav-dropdown">
        <button type="button" class="nav-link nav-dropdown-trigger${moreActive ? " nav-link-active" : ""}"
          aria-expanded="false" aria-haspopup="true" aria-controls="navMorePanel">
          Mehr ▾
        </button>
        <span id="navMorePanel" class="nav-dropdown-panel nav-more-panel hidden" role="menu">
          ${more.map((i) => link(i, "nav-dropdown-item")).join("")}
        </span>
      </span>`
    : "";

  const displayName = isAnonymizeMode()
    ? anon(window.__lynxUser?.display_name, "user")
    : window.__lynxUser?.display_name;
  const roleLabel =
    window.__lynxUser?.role_label ||
    ROLE_LABEL[window.__lynxUser?.role] ||
    window.__lynxUser?.role ||
    "User";
  const isAdminRole = window.__lynxUser?.role === "admin";

  const accountBit = window.__lynxUser
    ? `<span class="nav-dropdown nav-account">
        <button type="button" class="nav-link nav-dropdown-trigger nav-account-trigger${isAdminRole ? " nav-role-admin" : ""}"
          aria-expanded="false" aria-haspopup="true" aria-controls="navAccountPanel">
          ${escHtml(roleLabel)} ▾
        </button>
        <span id="navAccountPanel" class="nav-dropdown-panel nav-account-panel hidden" role="menu">
          <span class="nav-account-meta">${escHtml(displayName)} · <span class="${isAdminRole ? "nav-role-admin" : ""}">${escHtml(roleLabel)}</span></span>
          <a href="/logout" class="nav-link nav-dropdown-item" role="menuitem">Logout</a>
        </span>
      </span>`
    : "";

  nav.innerHTML = `
    <span class="nav-group nav-group-primary">${primary.map((i) => link(i)).join("")}</span>
    ${moreDropdown ? `<span class="nav-sep" aria-hidden="true"></span>${moreDropdown}` : ""}
    ${accountBit ? `<span class="nav-sep" aria-hidden="true"></span><span class="nav-group nav-group-user">${accountBit}</span>` : ""}
  `;

  ensureNavDocListeners();
  nav.querySelectorAll(".nav-dropdown").forEach((dd) => wireNavDropdown(dd));
}

initExpertModeToggle();
loadCurrentUser().then((u) => {
  renderSiteNav();
  if (typeof window.onLynxUserReady === "function") window.onLynxUserReady(u);
});
