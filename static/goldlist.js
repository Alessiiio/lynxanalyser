async function loadGoldlist() {
  const resp = await fetch("/api/goldlist");
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  renderGoldlist(data.domains || []);
}

function renderGoldlist(domains) {
  const list = document.getElementById("goldlistItems");
  const empty = document.getElementById("goldlistEmpty");
  const count = document.getElementById("goldlistCount");

  count.textContent = `${domains.length} ${domains.length === 1 ? "Eintrag" : "Einträge"}`;
  empty.classList.toggle("hidden", domains.length > 0);
  list.innerHTML = "";

  for (const domain of domains) {
    const li = document.createElement("li");
    li.className = "goldlist-item";
    li.innerHTML = `
      <span class="goldlist-item-domain">${escHtml(domain)}</span>
      <button type="button" class="btn-goldlist-remove" data-domain="${escHtml(domain)}" title="Entfernen">
        Entfernen
      </button>`;
    list.appendChild(li);
  }

  list.querySelectorAll(".btn-goldlist-remove").forEach((btn) => {
    btn.addEventListener("click", () => removeDomain(btn.dataset.domain));
  });
}

function showMessage(text, isError) {
  const el = document.getElementById("goldlistMessage");
  el.textContent = text;
  el.className = `goldlist-message ${isError ? "goldlist-message-error" : "goldlist-message-ok"}`;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4000);
}

document.getElementById("goldlistAddForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("goldlistDomainInput");
  let domain = input.value.trim().toLowerCase();
  domain = domain.replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
  if (!domain) return;

  try {
    const resp = await fetch("/api/goldlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    input.value = "";
    renderGoldlist(data.domains);
    showMessage(data.added ? `«${domain}» hinzugefügt` : `«${domain}» war bereits auf der Liste`, !data.added);
  } catch (err) {
    showMessage(`Fehler: ${err.message}`, true);
  }
});

async function removeDomain(domain) {
  if (!confirm(`«${domain}» wirklich von der Goldlist entfernen?`)) return;

  try {
    const resp = await fetch(`/api/goldlist?domain=${encodeURIComponent(domain)}`, {
      method: "DELETE",
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    renderGoldlist(data.domains);
    showMessage(`«${domain}» entfernt`, false);
  } catch (err) {
    showMessage(`Fehler: ${err.message}`, true);
  }
}

loadGoldlist().catch(() => {
  showMessage("Goldlist konnte nicht geladen werden.", true);
});
