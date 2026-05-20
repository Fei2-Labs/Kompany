// Cost preview modal — PR4 of PRD 05-19-onboard-v2-flow.
//
// Implements the PREVIEW layer of the three-layer cost visibility
// discipline (memory: [[engineering-cost-visibility-discipline]]). The
// founder types a draft prompt + max_output_tokens and we estimate
// (no LLM call) the cost using the same heuristics as the backend
// ``kompany.llm.cost_preview.preview_cost`` helper. The numbers stay
// on the client to avoid burning tokens on the modal itself.
//
// Token estimate: ~4 chars / token; output: max_output_tokens * 0.8.
// Pricing table is fetched on first mount from /onboarding/ping is too
// heavy (would need network). Instead we ship a tiny static table for
// the most common models; "other" prices fall back to a 100 / 1000 cap
// so we never under-promise burn.

const STATIC_PRICING = {
  // model name → [input_per_mtok, output_per_mtok]
  "claude-opus-4-7": [15.0, 75.0],
  "claude-haiku-4-20250414": [0.8, 4.0],
  "claude-sonnet-4-20250514": [3.0, 15.0],
  "gpt-4o": [2.5, 10.0],
  "gpt-4o-mini": [0.15, 0.6],
  "gemini-2.0-flash": [0.1, 0.4],
  "kimi-k2": [0.5, 2.0],
  "glm-4.6": [0.6, 2.2],
};

const DEFAULT_MODEL = "claude-haiku-4-20250414";

function estimateCost(inputTokens, outputTokens, model) {
  const px = STATIC_PRICING[model] || [3.0, 15.0]; // safe over-estimate
  return (inputTokens / 1_000_000) * px[0] + (outputTokens / 1_000_000) * px[1];
}

function estimateInputTokens(text) {
  if (!text) return 0;
  return Math.max(1, Math.floor(text.length / 4));
}

function estimateOutputTokens(maxOut) {
  if (!maxOut || maxOut <= 0) return 0;
  return Math.max(1, Math.floor(maxOut * 0.8));
}

function fmt(n) {
  const v = Number(n || 0);
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

let _modalEl = null;

export function openCostPreviewModal(options) {
  const opts = options || {};
  const currentBalance = Number(opts.currentBalance != null ? opts.currentBalance : 0);
  const initialPrompt = opts.prompt || "";
  const initialModel = opts.model || DEFAULT_MODEL;
  const initialMaxOut = opts.max_output_tokens || 600;

  if (_modalEl) {
    _modalEl.remove();
    _modalEl = null;
  }

  const modal = document.createElement("div");
  modal.className = "cost-modal-backdrop";
  modal.innerHTML = `
    <div class="cost-modal frame" data-label="COST_PREVIEW // estimate before spend">
      <div class="cost-modal-row">
        <label>MODEL</label>
        <select class="cost-modal-model">
          ${Object.keys(STATIC_PRICING).map((m) => `<option value="${escapeHtml(m)}" ${m === initialModel ? "selected" : ""}>${escapeHtml(m)}</option>`).join("")}
        </select>
      </div>
      <div class="cost-modal-row">
        <label>DRAFT PROMPT</label>
        <textarea class="cost-modal-prompt" rows="4" placeholder="Paste or type the directive you want to send...">${escapeHtml(initialPrompt)}</textarea>
      </div>
      <div class="cost-modal-row cost-modal-row-inline">
        <label>MAX OUTPUT TOKENS</label>
        <input class="cost-modal-maxout" type="number" min="1" step="50" value="${initialMaxOut}">
      </div>
      <div class="cost-modal-result" id="cost-modal-result"></div>
      <div class="cost-modal-actions">
        <button type="button" class="onb-btn cost-modal-close">[ close ]</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  _modalEl = modal;

  const modelEl = modal.querySelector(".cost-modal-model");
  const promptEl = modal.querySelector(".cost-modal-prompt");
  const maxOutEl = modal.querySelector(".cost-modal-maxout");
  const resultEl = modal.querySelector(".cost-modal-result");

  function update() {
    const inTok = estimateInputTokens(promptEl.value);
    const outTok = estimateOutputTokens(Number(maxOutEl.value));
    const cost = estimateCost(inTok, outTok, modelEl.value);
    const after = currentBalance - cost;
    resultEl.innerHTML = `
      <div><span>est. input tokens</span><b>${inTok}</b></div>
      <div><span>est. output tokens</span><b>${outTok}</b></div>
      <div><span>est. cost</span><b>${fmt(cost)}</b></div>
      <div><span>ledger now</span><b>${fmt(currentBalance)}</b></div>
      <div><span>ledger after</span><b>${fmt(after)}</b></div>
    `;
  }
  modelEl.addEventListener("change", update);
  promptEl.addEventListener("input", update);
  maxOutEl.addEventListener("input", update);
  update();

  function close() {
    modal.remove();
    _modalEl = null;
  }
  modal.querySelector(".cost-modal-close").addEventListener("click", close);
  modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  }, { once: true });
}
