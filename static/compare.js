document.getElementById("compareForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const urlA = document.getElementById("urlA").value.trim();
  const urlB = document.getElementById("urlB").value.trim();
  if (!urlA || !urlB) return;

  const btn = document.getElementById("compareBtn");
  btn.disabled = true;
  btn.textContent = "Vergleiche…";

  try {
    const params = new URLSearchParams({ url_a: urlA, url_b: urlB });
    const resp = await fetch(`/api/compare?${params}`, { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    renderCompare(data.report_a, data.report_b);
    document.getElementById("compareResults").classList.remove("hidden");
  } catch (err) {
    alert(`Vergleich fehlgeschlagen: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Vergleich starten";
  }
});

function renderCompare(a, b) {
  const grid = document.getElementById("compareGrid");
  grid.innerHTML = `
    ${renderCompareCard("A", a)}
    ${renderCompareCard("B", b)}
    ${renderDiffSummary(a, b)}
  `;
}

function renderCompareCard(label, report) {
  const flags = report.critical_flags?.length
    ? `<ul class="compare-flags">${report.critical_flags.map(f => `<li>${escHtml(f)}</li>`).join("")}</ul>`
    : `<p class="compare-muted">Keine kritischen Flags</p>`;

  return `
    <div class="compare-card">
      <div class="compare-card-label">Webseite ${label}</div>
      <div class="compare-domain">${escHtml(report.domain)}</div>
      <div class="compare-score color-${report.verdict_color}">${report.total_score}/100</div>
      <div class="compare-verdict color-${report.verdict_color}">${escHtml(translateVerdict(report.verdict))}</div>
      ${flags}
    </div>`;
}

function renderDiffSummary(a, b) {
  const diff = a.total_score - b.total_score;
  let text = "Beide Scores sind gleich.";
  if (diff > 0) text = `A liegt um ${diff} Punkte höher als B.`;
  if (diff < 0) text = `B liegt um ${Math.abs(diff)} Punkte höher als A.`;

  const onlyA = a.critical_flags.filter(f => !b.critical_flags.includes(f));
  const onlyB = b.critical_flags.filter(f => !a.critical_flags.includes(f));

  return `
    <div class="compare-summary">
      <strong>Differenz:</strong> ${escHtml(text)}
      ${onlyA.length ? `<div class="compare-diff-only">Nur A: ${onlyA.map(escHtml).join("; ")}</div>` : ""}
      ${onlyB.length ? `<div class="compare-diff-only">Nur B: ${onlyB.map(escHtml).join("; ")}</div>` : ""}
    </div>`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
