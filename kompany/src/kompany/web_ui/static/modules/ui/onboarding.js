// In-window onboarding wizard. Runs inside the Tauri WebView (or any
// browser) on first boot. Submits the form to POST /onboarding/complete
// and pivots to the main dashboard on success.

const form = document.getElementById("onboarding-form");
const submitBtn = document.getElementById("onb-submit");
const progress = document.getElementById("onb-progress");
const progressText = document.getElementById("onb-progress-text");
const errorBox = document.getElementById("onb-error");
const statusBadge = document.getElementById("onb-status");

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

  if (!body.provider || !body.api_key || !body.template_id) {
    showError("provider, api_key, and template_id are required");
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
