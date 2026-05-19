// In-window onboarding wizard. Runs inside the Tauri WebView (or any
// browser) on first boot. Submits the form to POST /onboarding/complete
// and pivots to the main dashboard on success.

const form = document.getElementById("onboarding-form");
const submitBtn = document.getElementById("onb-submit");
const progress = document.getElementById("onb-progress");
const progressText = document.getElementById("onb-progress-text");
const errorBox = document.getElementById("onb-error");
const statusBadge = document.getElementById("onb-status");
const providerInput = document.getElementById("onb-provider"); // hidden
const baseUrlField = document.getElementById("onb-base-url-field");
const baseUrlInput = document.getElementById("onb-base-url");

// Mission-targets task (05-19): the four quantitative target fields.
const budgetInput = document.getElementById("onb-budget");
const revenueInput = document.getElementById("onb-revenue-target");
const customerInput = document.getElementById("onb-customer-target");
const deadlineInput = document.getElementById("onb-deadline");

// Default the deadline to today + 90 days so a fresh founder isn't
// staring at an empty date picker. They can edit before submit.
function setDefaultDeadline() {
  if (!deadlineInput || deadlineInput.value) return;
  const d = new Date();
  d.setDate(d.getDate() + 90);
  // ``toISOString`` is UTC; we only want YYYY-MM-DD for the date input.
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  deadlineInput.value = `${yyyy}-${mm}-${dd}`;
}

// Whenever the template radio changes, fetch that template's manifest
// and populate budget / revenue / customer placeholders (and values, if
// they're still empty). Founder edits are preserved — we never
// overwrite a value the user typed.
async function syncTemplateDefaults(templateId) {
  if (!templateId) return;
  let manifest;
  try {
    const res = await fetch(
      `/templates/${encodeURIComponent(templateId)}`,
      { headers: { Accept: "application/json" } },
    );
    if (!res.ok) return;
    manifest = await res.json();
  } catch (_) {
    return;
  }
  if (!manifest || typeof manifest !== "object") return;
  if (budgetInput && manifest.initial_budget != null) {
    const v = String(manifest.initial_budget);
    budgetInput.placeholder = v;
    if (!budgetInput.value) budgetInput.value = v;
  }
  if (revenueInput && manifest.revenue_target != null) {
    const v = String(manifest.revenue_target);
    revenueInput.placeholder = v;
    if (!revenueInput.value) revenueInput.value = v;
  }
  if (customerInput) {
    if (manifest.customer_target != null) {
      const v = String(manifest.customer_target);
      customerInput.placeholder = v;
      if (!customerInput.value) customerInput.value = v;
    } else {
      customerInput.placeholder = "(optional)";
    }
  }
}

function syncBaseUrlVisibility() {
  const isCustom = providerInput.value === "custom";
  baseUrlField.hidden = !isCustom;
  baseUrlInput.required = isCustom;
}

// ---------- Cyberpunk combobox (replaces native <select>) ----------
function initCombobox(rootId) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const targetId = root.dataset.target;
  const hidden = document.getElementById(targetId);
  const button = root.querySelector(".cyb-combobox-button");
  const list = root.querySelector(".cyb-combobox-list");
  const options = Array.from(list.querySelectorAll(".cyb-combobox-option"));

  function setOpen(open) {
    root.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const sel = options.find((o) => o.dataset.value === hidden.value) || options[0];
      options.forEach((o) => o.removeAttribute("data-active"));
      sel.setAttribute("data-active", "true");
      sel.scrollIntoView({ block: "nearest" });
      document.addEventListener("click", onDocClick, true);
      document.addEventListener("keydown", onKeyDown, true);
    } else {
      document.removeEventListener("click", onDocClick, true);
      document.removeEventListener("keydown", onKeyDown, true);
    }
  }

  function selectOption(opt) {
    if (!opt) return;
    options.forEach((o) => o.removeAttribute("aria-selected"));
    opt.setAttribute("aria-selected", "true");
    hidden.value = opt.dataset.value;
    button.textContent = opt.textContent;
    hidden.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function onDocClick(evt) {
    if (!root.contains(evt.target)) setOpen(false);
  }

  function onKeyDown(evt) {
    const isOpen = root.getAttribute("aria-expanded") === "true";
    if (!isOpen) return;
    const active = list.querySelector('[data-active="true"]') || options[0];
    let idx = options.indexOf(active);
    if (evt.key === "Escape") {
      setOpen(false);
      button.focus();
      evt.preventDefault();
    } else if (evt.key === "ArrowDown") {
      idx = (idx + 1) % options.length;
      active.removeAttribute("data-active");
      options[idx].setAttribute("data-active", "true");
      options[idx].scrollIntoView({ block: "nearest" });
      evt.preventDefault();
    } else if (evt.key === "ArrowUp") {
      idx = (idx - 1 + options.length) % options.length;
      active.removeAttribute("data-active");
      options[idx].setAttribute("data-active", "true");
      options[idx].scrollIntoView({ block: "nearest" });
      evt.preventDefault();
    } else if (evt.key === "Enter" || evt.key === " ") {
      selectOption(options[idx]);
      setOpen(false);
      button.focus();
      evt.preventDefault();
    }
  }

  button.addEventListener("click", (evt) => {
    evt.preventDefault();
    setOpen(root.getAttribute("aria-expanded") !== "true");
  });
  options.forEach((opt) => {
    opt.addEventListener("click", (evt) => {
      evt.preventDefault();
      selectOption(opt);
      setOpen(false);
      button.focus();
    });
  });
}

initCombobox("onb-provider-combobox");
providerInput.addEventListener("change", syncBaseUrlVisibility);
syncBaseUrlVisibility();

// Mission-targets bootstrap: set the 90-day default + populate from the
// currently-selected starter template (whichever radio was pre-checked).
setDefaultDeadline();
const initialTemplate = form.querySelector(
  'input[name="template_id"]:checked',
);
if (initialTemplate) syncTemplateDefaults(initialTemplate.value);
form.querySelectorAll('input[name="template_id"]').forEach((radio) => {
  radio.addEventListener("change", (evt) => {
    if (evt.target.checked) syncTemplateDefaults(evt.target.value);
  });
});

let spinnerTimer = null;
const spinnerFrames = ["█", "▉", "▊", "▋", "▌", "▍", "▎", "▏"];

function startSpinner() {
  let i = 0;
  const el = progress.querySelector(".onb-spinner");
  spinnerTimer = setInterval(() => {
    if (el) el.textContent = spinnerFrames[i % spinnerFrames.length];
    i += 1;
  }, 80);
}

function stopSpinner() {
  if (spinnerTimer) {
    clearInterval(spinnerTimer);
    spinnerTimer = null;
  }
}

function setBusy(busy, message) {
  if (busy) {
    submitBtn.disabled = true;
    submitBtn.classList.add("busy");
    progress.hidden = false;
    progressText.textContent = message || "Provisioning your company...";
    statusBadge.textContent = "provisioning";
    errorBox.hidden = true;
    errorBox.textContent = "";
    startSpinner();
  } else {
    submitBtn.disabled = false;
    submitBtn.classList.remove("busy");
    progress.hidden = true;
    stopSpinner();
  }
}

function showError(message) {
  setBusy(false);
  errorBox.hidden = false;
  errorBox.textContent = "// ERROR: " + (message || "unknown error");
  statusBadge.textContent = "error";
}

async function handleSubmit(evt) {
  evt.preventDefault();
  const data = new FormData(form);
  const body = {
    provider: data.get("provider"),
    api_key: data.get("api_key"),
    template_id: data.get("template_id"),
  };
  const directive = (data.get("directive") || "").toString().trim();
  if (directive) body.directive = directive;
  const baseUrl = (data.get("base_url") || "").toString().trim();
  if (baseUrl) body.base_url = baseUrl;

  // Mission-targets task (05-19): the four quantitative fields. Empty
  // strings stay omitted so the server's "fall back to template
  // manifest" path stays the default.
  const budgetRaw = (data.get("initial_budget") || "").toString().trim();
  if (budgetRaw) body.initial_budget = Number(budgetRaw);
  const revRaw = (data.get("revenue_target") || "").toString().trim();
  if (revRaw) body.revenue_target = Number(revRaw);
  const custRaw = (data.get("customer_target") || "").toString().trim();
  if (custRaw) body.customer_target = Number(custRaw);
  const deadlineRaw = (data.get("deadline") || "").toString().trim();
  if (deadlineRaw) body.deadline = deadlineRaw;

  if (!body.provider || !body.api_key || !body.template_id) {
    showError("provider, api_key, and template_id are required");
    return;
  }
  if (body.provider === "custom" && !body.base_url) {
    showError("custom provider requires a base_url");
    return;
  }

  setBusy(true, "Booting C-suite agents...");

  let res;
  try {
    res = await fetch("/onboarding/complete", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    showError("network error: " + err.message);
    return;
  }

  if (!res.ok) {
    // 422 = pydantic validation; surface FastAPI's detail when present.
    let detail = "HTTP " + res.status;
    try {
      const payload = await res.json();
      if (payload && payload.detail) {
        detail = typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail);
      }
    } catch (_) { /* ignore */ }
    showError(detail);
    return;
  }

  let result;
  try {
    result = await res.json();
  } catch (err) {
    showError("malformed response: " + err.message);
    return;
  }

  if (result.status === "ready") {
    progressText.textContent = "Company online. Loading dashboard...";
    statusBadge.textContent = "ready";
    // Brief pause so the user sees the success state before pivot.
    setTimeout(() => {
      window.location.replace("/ui/");
    }, 600);
    return;
  }

  showError(result.message || result.code || "onboarding failed");
}

form.addEventListener("submit", handleSubmit);
