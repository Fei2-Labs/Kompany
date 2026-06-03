// Timeline presentation controller (Phase 2 of PRD 06-03-dashboard-no-scroll).
//
// Founder decision 2026-06-03: the LIVE TIMELINE is process noise, so it is
// hidden from the default dashboard. INBOX takes the full right column. The
// timeline can still be opened on demand as a modal overlay (custom DOM —
// window.confirm/alert are silently disabled in the Tauri WebView), and a
// "pin" toggle inside the modal docks it back as a panel below INBOX.
//
// The single #timeline element (rendered into by timeline.js) is physically
// MOVED between the modal body and the docked frame. That keeps timeline.js'
// rolling 100-line buffer + render logic untouched: pushTimeline() always
// targets #timeline, wherever it currently lives, so SSE events are never
// lost or rendered into a missing node.

const PIN_KEY = "kompany.timeline.pinned";

let _modalEl = null;
let _onKeydown = null;

function isPinned() {
  try {
    return localStorage.getItem(PIN_KEY) === "1";
  } catch (_) {
    return false;
  }
}

function setPinned(on) {
  try {
    localStorage.setItem(PIN_KEY, on ? "1" : "0");
  } catch (_) { /* private mode — non-fatal, falls back to session-only */ }
}

function timelineEl() {
  return document.getElementById("timeline");
}

function dockEl() {
  return document.getElementById("timeline-dock");
}

// Park the timeline back into its docked frame and show/hide the dock based
// on the pin state. Called when the modal closes and on initial load.
function applyDockState() {
  const dock = dockEl();
  const tl = timelineEl();
  if (!dock || !tl) return;
  if (tl.parentElement !== dock) {
    dock.appendChild(tl);
  }
  if (isPinned()) {
    dock.removeAttribute("hidden");
  } else {
    dock.setAttribute("hidden", "");
  }
}

function closeModal() {
  if (!_modalEl) return;
  // Move the live timeline node back to its dock FIRST. Removing the modal
  // while #timeline is still inside it would detach the timeline node too
  // (and getElementById would then return null), so reparent before remove.
  applyDockState();
  _modalEl.remove();
  _modalEl = null;
  if (_onKeydown) {
    document.removeEventListener("keydown", _onKeydown);
    _onKeydown = null;
  }
}

function openModal() {
  if (_modalEl) return;

  const modal = document.createElement("div");
  modal.className = "cost-modal-backdrop timeline-modal-backdrop";
  modal.innerHTML = `
    <div class="cost-modal frame timeline-modal" data-label="LIVE TIMELINE // process log">
      <div class="timeline-modal-body" id="timeline-modal-body"></div>
      <div class="cost-modal-actions timeline-modal-actions">
        <button type="button" class="onb-btn timeline-pin-btn"></button>
        <button type="button" class="onb-btn timeline-modal-close">[ close ]</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  _modalEl = modal;

  // Move the live #timeline node into the modal body (it carries the full
  // buffer + keeps receiving pushTimeline updates while open).
  const tl = timelineEl();
  const body = modal.querySelector("#timeline-modal-body");
  if (tl && body) {
    body.appendChild(tl);
    tl.scrollTop = tl.scrollHeight;
  }

  const pinBtn = modal.querySelector(".timeline-pin-btn");
  function syncPinBtn() {
    const pinned = isPinned();
    pinBtn.textContent = pinned ? "[ unpin ]" : "[ pin to dashboard ]";
    pinBtn.classList.toggle("is-pinned", pinned);
  }
  syncPinBtn();
  pinBtn.addEventListener("click", () => {
    setPinned(!isPinned());
    syncPinBtn();
  });

  modal.querySelector(".timeline-modal-close").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });
  _onKeydown = (e) => {
    if (e.key === "Escape") closeModal();
  };
  document.addEventListener("keydown", _onKeydown);
}

export function initTimelineModal() {
  // Restore persisted pin state on load.
  applyDockState();
  const fab = document.getElementById("timeline-fab");
  if (fab) fab.addEventListener("click", openModal);
}
