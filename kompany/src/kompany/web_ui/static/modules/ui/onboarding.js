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
