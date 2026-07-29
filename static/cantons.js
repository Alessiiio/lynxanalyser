/** Swiss canton coats of arms + names for Firmenübersicht. */

const CANTON_NAMES = {
  ZH: "Zürich", BE: "Bern", LU: "Luzern", UR: "Uri", SZ: "Schwyz",
  OW: "Obwalden", NW: "Nidwalden", GL: "Glarus", ZG: "Zug", FR: "Freiburg",
  SO: "Solothurn", BS: "Basel-Stadt", BL: "Basel-Landschaft", SH: "Schaffhausen",
  AR: "Appenzell AR", AI: "Appenzell IR", SG: "St. Gallen", GR: "Graubünden",
  AG: "Aargau", TG: "Thurgau", TI: "Tessin", VD: "Waadt", VS: "Wallis",
  NE: "Neuenburg", GE: "Genf", JU: "Jura",
};

/** Heraldic fallback colors when no image file is present. */
const CANTON_COLORS = {
  ZH: ["#248BCC", "#FFFFFF"], BE: ["#E30613", "#FFD200"], LU: ["#1E4B9C", "#FFFFFF"],
  UR: ["#FFD200", "#1A1A1A"], SZ: ["#E30613", "#FFFFFF"], OW: ["#E30613", "#FFFFFF"],
  NW: ["#E30613", "#FFFFFF"], GL: ["#E30613", "#FFFFFF"], ZG: ["#FFFFFF", "#1E4B9C"],
  FR: ["#1A1A1A", "#FFFFFF"], SO: ["#E30613", "#FFFFFF"], BS: ["#1A1A1A", "#FFFFFF"],
  BL: ["#E30613", "#FFFFFF"], SH: ["#FFD200", "#1A1A1A"], AR: ["#FFFFFF", "#1A1A1A"],
  AI: ["#FFFFFF", "#1A1A1A"], SG: ["#1B8F4A", "#FFFFFF"], GR: ["#1A1A1A", "#FFFFFF"],
  AG: ["#1A1A1A", "#1E4B9C"], TG: ["#1B8F4A", "#FFFFFF"], TI: ["#E30613", "#1E4B9C"],
  VD: ["#1B8F4A", "#FFFFFF"], VS: ["#E30613", "#FFFFFF"], NE: ["#1B8F4A", "#E30613"],
  GE: ["#FFD200", "#E30613"], JU: ["#E30613", "#FFFFFF"],
};

function normalizeCantonCode(code) {
  if (!code) return "";
  return String(code).trim().toUpperCase().slice(0, 2);
}

function cantonDisplayName(code) {
  const c = normalizeCantonCode(code);
  return CANTON_NAMES[c] || c;
}

function cantonFallbackSvg(code, size = 48) {
  const c = normalizeCantonCode(code) || "CH";
  const [a, b] = CANTON_COLORS[c] || ["#334155", "#94a3b8"];
  const h = Math.round(size * 1.2);
  // Unique gradient id per render
  const gid = `sh${c}${Math.random().toString(36).slice(2, 7)}`;
  return `<svg class="ca-wappen-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 80" width="${size}" height="${h}" aria-hidden="true">
    <defs>
      <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#fff" stop-opacity="0.2"/>
        <stop offset="100%" stop-color="#000" stop-opacity="0.15"/>
      </linearGradient>
    </defs>
    <path fill="${a}" d="M32 2 L58 12 V38 C58 58 32 76 32 76 C32 76 6 58 6 38 V12 Z"/>
    <path fill="${b}" d="M32 2 L58 12 V38 C58 58 32 76 32 76 V2 Z"/>
    <path fill="url(#${gid})" d="M32 2 L58 12 V38 C58 58 32 76 32 76 C32 76 6 58 6 38 V12 Z"/>
    <path fill="none" stroke="rgba(255,255,255,0.45)" stroke-width="1.5"
      d="M32 2 L58 12 V38 C58 58 32 76 32 76 C32 76 6 58 6 38 V12 Z"/>
    <text x="32" y="42" text-anchor="middle" fill="#0b0f14" font-family="Rajdhani,sans-serif"
      font-size="15" font-weight="700">${c}</text>
  </svg>`;
}

/**
 * Coat of arms markup. Prefers /static/cantons/{CODE}.png then .svg,
 * otherwise a heraldic color shield with canton code.
 */
function cantonWappenHtml(code, size = 48) {
  const c = normalizeCantonCode(code);
  if (!c) return "";
  const name = cantonDisplayName(c);
  return `<span class="ca-wappen" data-canton="${escHtml(c)}" title="Kanton ${escHtml(name)}">
    <img class="ca-wappen-img" src="/static/cantons/${escHtml(c)}.png" width="${size}" height="${size}"
      alt="Wappen ${escHtml(name)}" loading="lazy">
    <span class="ca-wappen-fallback hidden">${cantonFallbackSvg(c, size)}</span>
  </span>`;
}

function wireWappenImages(root = document) {
  root.querySelectorAll(".ca-wappen-img").forEach((img) => {
    if (img.dataset.wired) return;
    img.dataset.wired = "1";
    img.addEventListener("error", () => {
      const wrap = img.closest(".ca-wappen");
      const c = wrap?.dataset.canton;
      if (!c) return;
      if (!img.dataset.triedSvg) {
        img.dataset.triedSvg = "1";
        img.src = `/static/cantons/${c}.svg`;
        return;
      }
      img.classList.add("hidden");
      wrap?.querySelector(".ca-wappen-fallback")?.classList.remove("hidden");
    });
  });
}
