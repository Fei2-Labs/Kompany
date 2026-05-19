// Glossary page controller — wires the cyberpunk glossary.html template
// to the REST endpoints under /glossary. Plain ES modules; no build step.

const ROWS = document.getElementById("glossary-rows");
const FORM_TERM = document.getElementById("form-term");
const FORM_DEF = document.getElementById("form-definition");
const FORM_FORBID = document.getElementById("form-forbid");
const FORM_MSG = document.getElementById("form-msg");

function parseSynonyms(raw) {
  return (raw || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function setMessage(text, isError = false) {
  if (!FORM_MSG) return;
  FORM_MSG.textContent = text || "";
  FORM_MSG.style.color = isError ? "#ff8888" : "#ffd6a5";
}

function renderRows(rows) {
  if (!ROWS) return;
  ROWS.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="5" style="color:#a8b3cf;">No glossary terms yet — add one with the form on the right.</td>`;
    ROWS.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    const forbids = (row.forbidden_synonyms || []).join(", ") || "—";
    tr.innerHTML = `
      <td><b>${escapeHtml(row.term)}</b></td>
      <td>${escapeHtml(row.definition || "")}</td>
      <td>${escapeHtml(forbids)}</td>
      <td>${escapeHtml(row.added_by || "founder")}</td>
      <td class="row-actions">
        <button data-action="edit" data-term="${escapeAttr(row.term)}">edit</button>
        <button data-action="remove" data-term="${escapeAttr(row.term)}">remove</button>
      </td>
    `;
    ROWS.appendChild(tr);
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttr(s) {
  return escapeHtml(s).replaceAll('"', "&quot;");
}

async function reload() {
  try {
    const resp = await fetch("/glossary", { headers: { Accept: "application/json" } });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rows = await resp.json();
    renderRows(rows);
  } catch (err) {
    setMessage(`Failed to load glossary: ${err.message}`, true);
  }
}

async function handleAdd() {
  const term = (FORM_TERM.value || "").trim();
  const definition = (FORM_DEF.value || "").trim();
  if (!term || !definition) {
    setMessage("Both term and definition are required.", true);
    return;
  }
  try {
    const resp = await fetch("/glossary", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        term,
        definition,
        forbidden_synonyms: parseSynonyms(FORM_FORBID.value),
      }),
    });
    if (!resp.ok) {
      const payload = await resp.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${resp.status}`);
    }
    setMessage(`Added term '${term}'.`);
    clearForm();
    reload();
  } catch (err) {
    setMessage(err.message, true);
  }
}

async function handleUpdate() {
  const term = (FORM_TERM.value || "").trim();
  if (!term) {
    setMessage("Term required to update.", true);
    return;
  }
  const body = {};
  const definition = (FORM_DEF.value || "").trim();
  if (definition) body.definition = definition;
  const forbid = FORM_FORBID.value;
  if (forbid != null) body.forbidden_synonyms = parseSynonyms(forbid);
  try {
    const resp = await fetch(`/glossary/${encodeURIComponent(term)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const payload = await resp.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${resp.status}`);
    }
    setMessage(`Updated term '${term}'.`);
    clearForm();
    reload();
  } catch (err) {
    setMessage(err.message, true);
  }
}

async function handleRemove(term) {
  if (!confirm(`Remove glossary term '${term}'?`)) return;
  try {
    const resp = await fetch(`/glossary/${encodeURIComponent(term)}`, { method: "DELETE" });
    if (!resp.ok) {
      const payload = await resp.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${resp.status}`);
    }
    setMessage(`Removed term '${term}'.`);
    reload();
  } catch (err) {
    setMessage(err.message, true);
  }
}

async function handleEdit(term) {
  try {
    const resp = await fetch(`/glossary/${encodeURIComponent(term)}`, { headers: { Accept: "application/json" } });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const entry = await resp.json();
    FORM_TERM.value = entry.term || term;
    FORM_DEF.value = entry.definition || "";
    FORM_FORBID.value = (entry.forbidden_synonyms || []).join(", ");
    setMessage(`Loaded '${term}' — click Update to save changes.`);
  } catch (err) {
    setMessage(err.message, true);
  }
}

function clearForm() {
  FORM_TERM.value = "";
  FORM_DEF.value = "";
  FORM_FORBID.value = "";
}

document.getElementById("form-add").addEventListener("click", handleAdd);
document.getElementById("form-update").addEventListener("click", handleUpdate);
document.getElementById("form-clear").addEventListener("click", () => {
  clearForm();
  setMessage("");
});

if (ROWS) {
  ROWS.addEventListener("click", (ev) => {
    const button = ev.target.closest("button");
    if (!button) return;
    const action = button.dataset.action;
    const term = button.dataset.term;
    if (!action || !term) return;
    if (action === "edit") handleEdit(term);
    if (action === "remove") handleRemove(term);
  });
}

reload();
