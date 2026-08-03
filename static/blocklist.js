const BLOCKLIST_CATEGORY_LABELS = {
  investment_fraud: "Anlagebetrug",
  phishing_impersonation: "Phishing/Identitätsmissbrauch",
  support_scam: "Support-/Tech-Betrug",
  booking_scam: "Vorschussbetrug Buchung",
  marketplace_scam: "Marktplatz-Betrug",
  fake_shop: "Fake-Shop",
  general_suspicious: "Mehrere Warnsignale",
};

async function loadBlocklist() {
  const resp = await fetch("/api/blocklist");
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  renderBlocklist(data.entries || []);
}

function renderBlocklist(entries) {
  const list = document.getElementById("blocklistItems");
  const empty = document.getElementById("blocklistEmpty");
  const count = document.getElementById("blocklistCount");

  count.textContent = `${entries.length} ${entries.length === 1 ? "Eintrag" : "Einträge"}`;
  empty.classList.toggle("hidden", entries.length > 0);
  list.innerHTML = "";

  for (const entry of entries) {
    const domain = entry.domain || "";
    const category = BLOCKLIST_CATEGORY_LABELS[entry.fraud_category] || entry.fraud_category || "—";
    const note = (entry.note || "").trim();
    const when = entry.confirmed_at
      ? formatDateTimeDisplay(entry.confirmed_at)
      : "";

    const li = document.createElement("li");
    li.className = "blocklist-item";
    li.innerHTML = `
      <div class="blocklist-item-main">
        <span class="blocklist-item-domain">${escHtml(domain)}</span>
        <span class="blocklist-item-category">${escHtml(category)}</span>
        ${note ? `<p class="blocklist-item-note">${escHtml(note)}</p>` : ""}
        ${when ? `<p class="blocklist-item-meta">Bestätigt: ${escHtml(when)}</p>` : ""}
      </div>
      <button type="button" class="btn-blocklist-remove" data-domain="${escHtml(domain)}" title="Entfernen">
        Entfernen
      </button>`;
    list.appendChild(li);
  }

  list.querySelectorAll(".btn-blocklist-remove").forEach((btn) => {
    btn.addEventListener("click", () => removeBlocklistDomain(btn.dataset.domain));
  });
}

async function removeBlocklistDomain(domain) {
  if (!confirm(`«${domain}» wirklich von der Blocklist entfernen?`)) return;

  try {
    const resp = await fetch(`/api/blocklist?domain=${encodeURIComponent(domain)}`, {
      method: "DELETE",
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    renderBlocklist(data.entries || []);
  } catch (err) {
    alert(`Fehler: ${err.message}`);
  }
}

loadBlocklist().catch(() => {
  alert("Blocklist konnte nicht geladen werden.");
});
