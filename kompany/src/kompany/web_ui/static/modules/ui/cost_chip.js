// LLM_SPEND chip — dashboard header widget for the cost-visibility
// discipline (PR4 of PRD 05-19-onboard-v2-flow). Shows the running
// total spend in real USD; subscribes to ``llm.spend`` SSE for live
// increments and reconciles against ``GET /llm/spend/summary`` on mount
// + window focus so an inactive tab doesn't accumulate stale state.
//
// Click → opens the cost preview modal (see ``cost_preview_modal.js``)
// pre-filled with the current ledger balance + a sensible default
// prompt size; founders use it to sanity-check "if I run this next
// directive, what will it cost?" before pressing enter.
//
// The chip is rendered into ``#stat-llmspend`` in ``index.html``.

import { openCostPreviewModal } from "./cost_preview_modal.js";

function fmt(n) {
  const v = Number(n || 0);
  if (v >= 100) return `$${v.toFixed(0)}`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`;
}

class CostChip {
  constructor(el) {
    this.el = el;
    this.total = 0;
    this.row_count = 0;
    this._lastReconcileAt = 0;
    if (this.el) {
      this.el.style.cursor = "pointer";
      this.el.title = "Click to preview the next call's cost";
      this.el.addEventListener("click", () => this._openModal());
    }
  }

  render() {
    if (!this.el) return;
    this.el.textContent = fmt(this.total);
    this.el.classList.toggle("ok", this.total < 0.01);
    this.el.classList.toggle("warn", this.total >= 0.01 && this.total < 1);
    this.el.classList.toggle("hot", this.total >= 1);
  }

  onSpend(payload) {
    if (!payload || typeof payload !== "object") return;
    const delta = Number(payload.cost_usd || 0);
    if (Number.isFinite(delta) && delta > 0) {
      this.total += delta;
      this.row_count += 1;
      this.render();
    }
  }

  async reconcile() {
    // Cheap rate-limit: don't hammer the endpoint on focus chatter.
    const now = Date.now();
    if (now - this._lastReconcileAt < 2000) return;
    this._lastReconcileAt = now;
    try {
      const res = await fetch("/llm/spend/summary");
      if (!res.ok) return;
      const data = await res.json();
      this.total = Number(data.total_usd || 0);
      this.row_count = Number(data.row_count || 0);
      this.render();
    } catch (_) {
      /* keep current state — SSE will recover us */
    }
  }

  _openModal() {
    openCostPreviewModal({ currentBalance: -this.total });
  }
}

let _chip = null;

export function initCostChip() {
  const el = document.getElementById("stat-llmspend");
  if (!el) return null;
  _chip = new CostChip(el);
  _chip.render();
  _chip.reconcile();
  window.addEventListener("focus", () => _chip.reconcile());
  return _chip;
}

export function getCostChip() {
  return _chip;
}
