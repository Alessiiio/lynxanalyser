# Lynx — Design System (v1, Audit-basiert)

**Zweck:** Diese Datei ist die Referenz für jede neue UI-Änderung — insbesondere für Cursor-Prompts.
Faustregel: *bevor du eine neue Klasse erfindest, schau hier nach, ob es schon eine gibt.*

---

## Warum diese Datei existiert (Audit-Befund, 13.08.2026)

`static/style.css` ist ~9'940 Zeilen groß und über viele Feature-Sessions gewachsen. Konkret gefunden:

- **33 verschiedene Button-Klassen** (`btn-nav`, `btn-check`, `btn-case-confirm`, `ca-btn-fraud`, `docs-wiz-btn`, `hr-deep-search-btn`, `profiler-step-btn`, …), viele mit fast identischem Zweck.
- Bei den Buttons streuen **padding** (`0.4rem 0.75rem` bis `0.75rem 1.75rem`), **font-size** (`0.8rem` bis `1rem`) und **font-weight** (`600`, `650`, `700`) ohne erkennbares System.
- **68+ Badge/Pill/Tag/Chip-Klassen** (`badge-*`, `ca-*-badge`, `watch-*-pill`, `verdict-pill-*`, `docs-wiz-pill`, …) — keine gemeinsame Basisklasse, jede Seite baut Form/Abstand neu.
- **30+ Card/Panel-Klassen** mit eigenem Padding/Radius/Shadow statt einer gemeinsamen Basis.
- **`border-radius` in 13 verschiedenen Werten** parallel im Einsatz: `var(--radius-sm)`, `var(--radius)`, plus hartkodiert `2px, 3px, 4px, 6px, 7px, 8px, 10px, 12px, 50%, 999px` — teils sogar mit Fallback-Werten, die dem echten Token widersprechen (`var(--radius-sm, 8px)`, obwohl `--radius-sm: 4px` ist).

**Gute Nachricht:** Das Farb-/Schatten-/Font-System in `:root` ist bereits sauber (siehe unten) — das Problem sitzt ausschließlich auf der Komponenten-Ebene, nicht bei den Grund-Tokens.

---

## 1. Tokens (bereits vorhanden, unverändert — `:root` in style.css)

```css
--c-primary: #00d4ff;      --c-primary-light: rgba(0,212,255,.12)
--c-green:   #3dff8a;      --c-green-light:   rgba(61,255,138,.12)
--c-yellow:  #ffd54a;      --c-yellow-light:  rgba(255,213,74,.12)
--c-orange:  #ff8c42;      --c-orange-light:  rgba(255,140,66,.12)
--c-red:     #ff4d6d;      --c-red-light:     rgba(255,77,109,.14)
--c-gray:    #8b9cb3;      --c-gray-light:    rgba(139,156,179,.1)

--c-text: #e8edf4;   --c-muted: #8b9cb3;
--c-border: rgba(0,212,255,.16);
--c-bg: #0a0e14;  --c-card: #121a24;  --c-card-hover: #1a2433;

--radius: 6px;  --radius-sm: 4px;
--shadow: 0 2px 16px rgba(0,0,0,.45);
--shadow-md: 0 8px 32px rgba(0,0,0,.55);
--glow-cyan: 0 0 24px rgba(0,212,255,.2);

--font-display: "Rajdhani", system-ui, sans-serif;
--font-mono: "JetBrains Mono", "SF Mono", Consolas, monospace;
--font-body: "Rajdhani", system-ui, sans-serif;
```

### Neu ergänzt: `--radius-pill`

Die 13 Radius-Werte werden auf **3 Stufen** reduziert. Bei jeder neuen Komponente nur diese drei nutzen (bereits in `style.css` ergänzt):

```css
--radius-sm: 4px;      /* kleine Elemente: Chips, Inputs, Buttons */
--radius:    6px;      /* Standard: Cards, Panels, Modals (unverändert) */
--radius-pill: 999px;  /* neu — Pills/Badges mit rundem Rand */
```

*(Bewusst `--radius` bei 6px belassen, nicht auf den häufigeren 8px-Wert geändert — das würde 16 bestehende Stellen optisch verschieben. Nur `--radius-pill` ist neu dazugekommen, rein additiv.)*

Spacing: durchgehend das bestehende `rem`-Raster nutzen, keine neuen `px`-Werte für Abstände:

```
0.25rem · 0.5rem · 0.75rem · 1rem · 1.5rem · 2rem · 3rem
```

---

## 2. Buttons — kanonisch: `.btn` + Modifier

**Ziel:** Die 33 Klassen langfristig auf diese Basis + Modifier zurückführen. Neue Buttons **nur** so bauen:

```html
<button class="btn">Standard</button>
<button class="btn btn-primary">Primäraktion</button>
<button class="btn btn-danger">Löschen / Gefährlich</button>
<button class="btn btn-ghost">Sekundär, unauffällig</button>
<button class="btn btn-sm">Kompakt (in Tabellen/Listen)</button>
```

```css
.btn {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.55rem 1.1rem;
  border-radius: var(--radius-sm);
  font-family: var(--font-body);
  font-size: 0.85rem;
  font-weight: 600;
  border: 1px solid var(--c-border);
  background: var(--c-card);
  color: var(--c-text);
  cursor: pointer;
  transition: background .15s ease, border-color .15s ease;
}
.btn:hover { background: var(--c-card-hover); }
.btn:disabled { opacity: .5; cursor: not-allowed; }

.btn-primary { background: var(--c-primary-light); border-color: var(--c-primary); color: var(--c-primary); }
.btn-danger  { background: var(--c-red-light); border-color: var(--c-red); color: var(--c-red); }
.btn-ghost   { background: transparent; border-color: transparent; }
.btn-sm      { padding: 0.4rem 0.75rem; font-size: 0.8rem; }
```

**Migration, nicht Big-Bang:** Bestehende Klassen (`btn-nav`, `btn-check`, `ca-btn-fraud`, …) bleiben vorerst bestehen und funktionieren weiter. **Admin (`/admin`) bleibt bewusst bei `fraud-panel` / `btn-nav` / `btn-check` / `btn-case-*`** — die hellen Design-System-`.btn`-Klassen wirken dort auf dem dunklen Lynx-UI schlecht. Regel sonst: *sobald du eine Seite ohnehin anfasst und das dunkle Lynx-Look bestätigt ist, kannst du schrittweise `.btn` + Modifier nutzen — nicht ungeprüft ersetzen.*

---

## 3. Badges / Pills / Tags — kanonisch: `.badge` + Farb-Modifier

Die Farblogik (`badge-green`, `badge-red`, …) ist bereits gut und bleibt. Es fehlt nur die gemeinsame Form-Basis:

```html
<span class="badge badge-green">Aktiv</span>
<span class="badge badge-red">Kritisch</span>
<span class="badge badge-gray">Neutral</span>
```

```css
.badge {
  display: inline-flex; align-items: center;
  padding: 0.15rem 0.6rem;
  border-radius: var(--radius-pill);
  font-size: 0.75rem;
  font-weight: 650;
  line-height: 1.4;
}
/* Farb-Modifier bleiben wie bisher: */
.badge-green  { background: var(--c-green-light);  color: var(--c-green); }
.badge-yellow { background: var(--c-yellow-light); color: var(--c-yellow); }
.badge-orange { background: var(--c-orange-light); color: var(--c-orange); }
.badge-red    { background: var(--c-red-light);    color: var(--c-red); }
.badge-gray   { background: var(--c-gray-light);   color: var(--c-gray); }
```

Alle anderen Pill/Chip/Tag-Klassen (`verdict-pill-*`, `watch-meta-pill--*`, `ca-tl-chip*`, `docs-wiz-pill`, …) sind Kandidaten, auf `.badge` + Modifier zu wechseln, sobald die jeweilige Seite bearbeitet wird.

---

## 4. Cards / Panels — kanonisch: `.card`

```css
.card {
  background: var(--c-card);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.25rem;
}
.card-hover:hover { background: var(--c-card-hover); }
```

**Compact metric pills** (additiv zu `.card-stat`, in `style.css`): `.card-stat-pill` — inline Icon + Wert + Label, `border-radius: var(--radius-pill)`. Modifier: `.card-stat-pill--alert` (`--c-orange`), `.card-stat-pill--watch` (`--c-primary`).

Bestehende Panels (`fraud-panel`, `ca-suchweite-card`, `admin-panel`, `check-card`, …) behalten ihre inhaltsspezifischen Zusatzklassen für Innenaufbau, sollten aber `.card` als Basis referenzieren statt Padding/Radius/Shadow erneut zu definieren.

---

## 5. Regeln für Cursor-Prompts

Diese Sätze kannst du direkt in deine Cursor-Prompts kopieren, wenn du UI baust:

> Nutze für Buttons `.btn` + Modifier (`.btn-primary`, `.btn-danger`, `.btn-ghost`, `.btn-sm`) aus `DESIGN_SYSTEM.md`. Erfinde keine neue Button-Klasse, außer es gibt einen echten strukturellen Grund — dann in `DESIGN_SYSTEM.md` ergänzen.
>
> Für Status-/Tag-Anzeigen nutze `.badge` + Farb-Modifier (`badge-green/yellow/orange/red/gray`), nicht eine neue Pill-Klasse pro Feature.
>
> `border-radius` nur aus `--radius-sm` (4px), `--radius` (8px) oder `--radius-pill` (999px) — keine neuen Hardcode-Werte.
>
> Abstände nur aus dem bestehenden `rem`-Raster (0.25 / 0.5 / 0.75 / 1 / 1.5 / 2 / 3rem).

---

## 6. Priorisierter Rollout-Vorschlag

Nicht alles auf einmal. Reihenfolge nach Sichtbarkeit/Demo-Relevanz:

1. **Firmenanalyse** (`company-analysis.html/js`) — meistgenutzte, meistgezeigte Seite, hat mit `ca-*` die meisten eigenen Badge/Button-Varianten.
2. **Fraudfall** (`case.html/js`) — zweithäufigster Touchpoint, `btn-case-*` konsolidieren.
3. **Watchlist** (`watchlist.html/js`) — `watch-*-pill`-Familie auf `.badge` ziehen.
4. Rest (Admin, Profiler, Docs-Wizard) opportunistisch, wenn ohnehin bearbeitet.

Trag das am besten als eigenen Eintrag in `PLANNING.md` ein (Status `planned`, Tag `design-debt`), damit es nicht zwischen Feature-Arbeit untergeht.
