// Team feasibility review UI — task 05-19 feasibility-review-debate.
//
// Renders the CFO/CoS/CEO trio's evidence-traced claims as 3 columns,
// subscribes to ``llm.spend`` SSE for per-agent + total cost meters,
// and provides the [ADOPT TEAM PROPOSAL] / [COUNTER-PROPOSE] /
// [KEEP MY NUMBERS] founder controls + a free-text counter-proposal
// box that re-runs the team.
//
// When a new round arrives after a revise (``payload.rounds.length >
// previous``), the component diffs round-N against round-(N+1) per
// agent and renders red/green inline annotations:
//   * Added claim   → ``+`` marker, green tint.
//   * Removed claim → ``-`` marker, grey strikethrough.
//   * Stable claim  → no marker.
//
// Iteration 4+ (``payload.ceo_only === true``): the banner above the
// trio reads "team meeting condensed to CEO-only updates", CFO/CoS
// columns get a "frozen at round 3" tag and a grey overlay.
//
// No build step — vanilla ES module. Mounted by the onboarding page
// or any host UI that wants the review screen.

import { renderClaimList } from "./claim_list.js";
import { connectSSE } from "../sse.js";

const ROLES = [
  { key: "cfo", label: "CFO" },
  { key: "cos", label: "CoS" },
  { key: "ceo", label: "CEO" },
];

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatUsd(amount) {
  const n = Number(amount || 0);
  if (Math.abs(n) < 0.005) return "$0.00";
  return `$${n.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`;
}

// ---------------------------------------------------------------------------
// Diff helper
// ---------------------------------------------------------------------------

/**
 * Diff two claim lists by their text content.
 * Returns ``{added, removed, stable}`` each a list of claim dicts.
 *
 * We use claim ``text`` as the equality key. This is simple but matches
 * the LLM contract: claims are atomic statements; identical text means
 * the agent didn't change its position on that point.
 */
export function diffClaims(prevClaims, nextClaims) {
  const prev = Array.isArray(prevClaims) ? prevClaims : [];
  const next = Array.isArray(nextClaims) ? nextClaims : [];
  const prevByText = new Map();
  for (const c of prev) prevByText.set((c.text || "").trim(), c);
  const nextByText = new Map();
  for (const c of next) nextByText.set((c.text || "").trim(), c);

  const added = [];
  const stable = [];
  for (const c of next) {
    const t = (c.text || "").trim();
    if (prevByText.has(t)) stable.push(c);
    else added.push(c);
  }
  const removed = [];
  for (const c of prev) {
    const t = (c.text || "").trim();
    if (!nextByText.has(t)) removed.push(c);
  }
  return { added, removed, stable };
}

// ---------------------------------------------------------------------------
// Per-agent cost meter
// ---------------------------------------------------------------------------

class CostMeter {
  constructor(role) {
    this.role = role;
    this.input = 0;
    this.output = 0;
    this.cost = 0;
    this.calls = 0;
    this.lastBalance = null;
  }

  applySpend(payload) {
    if (!payload || typeof payload !== "object") return;
    // We accept spend tagged target_feasibility OR feasibility_revise.
    const at = String(payload.action_type || "");
    if (at !== "target_feasibility" && at !== "feasibility_revise") return;
    // The SSE envelope doesn't carry an explicit role; we use a
    // round-robin attribution where the UI advances the active role as
    // events arrive. The caller is responsible for selecting the right
    // meter (see TeamCostBoard).
    this.input += Number(payload.input_tokens || 0);
    this.output += Number(payload.output_tokens || 0);
    this.cost += Number(payload.cost_usd || 0);
    this.calls += 1;
    if (payload.ledger_balance_after != null) {
      this.lastBalance = Number(payload.ledger_balance_after);
    }
  }

  renderInto(container) {
    container.innerHTML = "";
    const head = document.createElement("div");
    head.className = "fr-cost-row";
    head.textContent =
      `${this.input} in / ${this.output} out · ${formatUsd(this.cost)}` +
      (this.lastBalance != null ? ` · LEDGER ${formatUsd(this.lastBalance)}` : "");
    container.appendChild(head);
  }
}

// ---------------------------------------------------------------------------
// Team cost board — aggregates per-role meters + total
// ---------------------------------------------------------------------------

class TeamCostBoard {
  constructor(roles) {
    this.meters = new Map();
    for (const r of roles) this.meters.set(r.key, new CostMeter(r.key));
    // SSE events don't carry a role tag — we attribute them round-robin
    // in CFO → CoS → CEO order. Round 4+ (CEO-only) we skip CFO/CoS.
    this._nextRoleIdx = 0;
    this._order = roles.map((r) => r.key);
    this._ceoOnly = false;
  }

  setCeoOnly(flag) {
    this._ceoOnly = !!flag;
    this._nextRoleIdx = 0;
  }

  onSpend(payload) {
    const at = String(payload && payload.action_type || "");
    if (at !== "target_feasibility" && at !== "feasibility_revise") return;
    let roleKey;
    if (this._ceoOnly) {
      roleKey = "ceo";
    } else {
      roleKey = this._order[this._nextRoleIdx % this._order.length];
      this._nextRoleIdx += 1;
    }
    const m = this.meters.get(roleKey);
    if (m) m.applySpend(payload);
  }

  totalCost() {
    let t = 0;
    for (const m of this.meters.values()) t += m.cost;
    return t;
  }

  lastBalance() {
    let bal = null;
    for (const m of this.meters.values()) {
      if (m.lastBalance != null) bal = m.lastBalance;
    }
    return bal;
  }
}

// ---------------------------------------------------------------------------
// Renderer
// ---------------------------------------------------------------------------

function renderColumn(root, role, payload, prevPayload) {
  root.innerHTML = "";
  root.className = "fr-col";
  root.dataset.role = role.key;

  const head = document.createElement("div");
  head.className = "fr-col-head";
  head.textContent = `[${role.label}]`;
  root.appendChild(head);

  const costRow = document.createElement("div");
  costRow.className = "fr-col-cost";
  root.appendChild(costRow);

  const ceoOnly = payload && payload.ceo_only;
  const frozen = ceoOnly && (role.key === "cfo" || role.key === "cos");
  if (frozen) {
    root.classList.add("fr-col-frozen");
    const tag = document.createElement("div");
    tag.className = "fr-col-frozen-tag";
    tag.textContent = "frozen from round 3";
    root.appendChild(tag);
  }

  const claimsKey = `${role.key}_claims`;
  const claims = (payload && payload[claimsKey]) || [];
  const prevClaims = (prevPayload && prevPayload[claimsKey]) || [];

  // Diff against previous round when present.
  const hasPrev = prevPayload != null && Array.isArray(prevPayload[claimsKey]);
  if (hasPrev) {
    const diff = diffClaims(prevClaims, claims);
    // Render removed first (greyed), then stable, then added (greened).
    if (diff.removed.length > 0) {
      const remHead = document.createElement("div");
      remHead.className = "fr-diff-section fr-diff-removed-head";
      remHead.textContent = "was:";
      root.appendChild(remHead);
      const removedBox = document.createElement("div");
      removedBox.className = "fr-diff-removed";
      renderClaimList(removedBox, diff.removed);
      root.appendChild(removedBox);
    }
    const nowHead = document.createElement("div");
    nowHead.className = "fr-diff-section fr-diff-now-head";
    nowHead.textContent = "now:";
    root.appendChild(nowHead);
    if (diff.added.length > 0) {
      const addedBox = document.createElement("div");
      addedBox.className = "fr-diff-added";
      renderClaimList(addedBox, diff.added);
      root.appendChild(addedBox);
    }
    if (diff.stable.length > 0) {
      const stableBox = document.createElement("div");
      stableBox.className = "fr-diff-stable";
      renderClaimList(stableBox, diff.stable);
      root.appendChild(stableBox);
    }
    if (
      diff.added.length === 0 &&
      diff.stable.length === 0 &&
      diff.removed.length === 0
    ) {
      const empty = document.createElement("div");
      empty.className = "fr-diff-empty";
      empty.textContent = "(no change)";
      root.appendChild(empty);
    }
  } else {
    const box = document.createElement("div");
    box.className = "fr-claims";
    renderClaimList(box, claims);
    root.appendChild(box);
  }

  return { costRow };
}

function renderHeader(root, payload) {
  root.innerHTML = "";
  const gen = (payload && payload.generation) || 1;
  const ceoOnly = !!(payload && payload.ceo_only);
  const isRevise = gen > 1;
  const tag = ceoOnly
    ? `ROUND ${gen} · CEO-ONLY UPDATES`
    : isRevise
      ? `ROUND ${gen} · REBUTTAL ← REVISED`
      : `ROUND ${gen} · POSITIONS`;
  root.textContent = `// TEAM FEASIBILITY REVIEW   ${tag}`;
}

function renderIterationBanner(root, payload) {
  root.innerHTML = "";
  const ceoOnly = !!(payload && payload.ceo_only);
  if (!ceoOnly) {
    root.hidden = true;
    return;
  }
  root.hidden = false;
  root.textContent =
    "▾ Revision 4+: team meeting condensed to CEO-only updates. " +
    "CFO/CoS views frozen from Round 3. CEO will respond solo to your " +
    "counter-proposals.";
}

// ---------------------------------------------------------------------------
// Public mount
// ---------------------------------------------------------------------------

/**
 * Mount the feasibility review screen.
 *
 * @param {HTMLElement} host — empty container to populate.
 * @param {object} options
 *   * approval: ApprovalRequest dict (the one returned by
 *     ``run_target_feasibility_review`` / ``request_approval_revision``).
 *   * onAdopt: () => void  — founder accepts team proposal.
 *   * onKeep: () => void   — founder keeps original numbers.
 *   * onCounter: (text:string) => Promise<object>  — founder counter-
 *     proposes; resolves to the new approval dict.
 *   * sseUrl: string       — SSE endpoint URL (default ``"/events"``).
 *   * priorPayload: object — payload from the previous approval in the
 *     thread (used for diff). Optional; auto-derived from
 *     ``approval.payload.rounds`` when not supplied.
 */
export function mountFeasibilityReview(host, options) {
  const opts = options || {};
  const approval = opts.approval || {};
  let payload = approval.payload || {};

  host.innerHTML = "";
  host.classList.add("fr-host");

  const header = document.createElement("div");
  header.className = "fr-header";
  host.appendChild(header);

  // Top-of-panel summary that names the actual numbers behind ADOPT vs
  // KEEP. Populated lazily via setTargetsBundle so the mount doesn't
  // block on /targets — the prose columns still render immediately.
  const proposalCard = document.createElement("div");
  proposalCard.className = "fr-proposal-card";
  proposalCard.hidden = true;
  host.appendChild(proposalCard);

  const banner = document.createElement("div");
  banner.className = "fr-banner";
  host.appendChild(banner);

  const grid = document.createElement("div");
  grid.className = "fr-grid";
  host.appendChild(grid);

  const colsByRole = {};
  for (const role of ROLES) {
    const col = document.createElement("div");
    grid.appendChild(col);
    colsByRole[role.key] = col;
  }

  const total = document.createElement("div");
  total.className = "fr-total";
  host.appendChild(total);

  // Instruction sentence so the founder doesn't have to infer the
  // decision shape from button labels alone. Keep terse — three
  // verbs, one line.
  const hint = document.createElement("div");
  hint.className = "fr-hint";
  hint.innerHTML =
    "Team reviewed your numbers. Pick one: " +
    "<b>keep</b> yours, <b>adopt</b> theirs, or " +
    "<b>counter</b> with new info.";
  host.appendChild(hint);

  const actions = document.createElement("div");
  actions.className = "fr-actions";

  function makeBtn(label, cls, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `fr-btn ${cls}`;
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  // Visual hierarchy: KEEP MY NUMBERS = primary (green). Founder
  // authority is Kompany's product story default — team challenges,
  // founder decides. ADOPT (blue) + COUNTER (amber) are secondary,
  // equally available, less assertive.
  const keepBtn = makeBtn(
    "[ ▸ KEEP MY NUMBERS ]",
    "fr-btn-keep",
    () => opts.onKeep && opts.onKeep(),
  );
  const adoptBtn = makeBtn(
    "[ ADOPT TEAM PROPOSAL ]",
    "fr-btn-adopt",
    () => opts.onAdopt && opts.onAdopt(),
  );
  const counterBtn = makeBtn(
    "[ COUNTER-PROPOSE ]",
    "fr-btn-counter",
    () => toggleCounter(true),
  );
  actions.appendChild(keepBtn);
  actions.appendChild(adoptBtn);
  actions.appendChild(counterBtn);
  host.appendChild(actions);

  const counterBox = document.createElement("div");
  counterBox.className = "fr-counter";
  counterBox.hidden = true;
  const counterText = document.createElement("textarea");
  counterText.className = "fr-counter-text";
  counterText.placeholder =
    "I have part-time consulting income covering my burn. " +
    "Treat $50 as a project-only budget for tools/ads.";
  const counterSend = makeBtn("[ SEND TO TEAM ]", "fr-btn-send", async () => {
    const text = (counterText.value || "").trim();
    if (!text) return;
    counterSend.disabled = true;
    counterBtn.disabled = true;
    counterBox.classList.add("fr-counter-busy");
    try {
      if (opts.onCounter) {
        const newApproval = await opts.onCounter(text);
        if (newApproval) {
          // Replace the current view with the new round + diff
          // against the prior payload.
          rerender(newApproval, payload);
          payload = newApproval.payload || {};
          counterText.value = "";
          toggleCounter(false);
        }
      }
    } finally {
      counterSend.disabled = false;
      counterBtn.disabled = false;
      counterBox.classList.remove("fr-counter-busy");
    }
  });
  counterBox.appendChild(counterText);
  counterBox.appendChild(counterSend);
  host.appendChild(counterBox);

  function toggleCounter(open) {
    counterBox.hidden = !open;
    if (open) counterText.focus();
  }

  // Cost board: per-role meters + total. Attached to the SSE endpoint
  // for live updates whenever an llm.spend event lands.
  const board = new TeamCostBoard(ROLES);

  // ---- core re-render ----
  function rerender(newApproval, prevPayloadForDiff) {
    const ap = newApproval || approval;
    const pp = ap.payload || {};
    renderHeader(header, pp);
    renderIterationBanner(banner, pp);
    board.setCeoOnly(!!pp.ceo_only);
    // Reset board meters when we move to a new round so the per-role
    // numbers reflect the just-completed run, not the cumulative chain.
    board.meters.forEach((m) => {
      m.input = 0; m.output = 0; m.cost = 0; m.calls = 0; m.lastBalance = null;
    });
    for (const role of ROLES) {
      const { costRow } = renderColumn(
        colsByRole[role.key], role, pp, prevPayloadForDiff || null,
      );
      board.meters.get(role.key).renderInto(costRow);
    }
    total.textContent =
      `TOTAL THIS REVIEW: ${formatUsd(board.totalCost())}` +
      (board.lastBalance() != null
        ? ` · LEDGER NOW ${formatUsd(board.lastBalance())}`
        : "");
  }

  // Initial render — derive prior payload from rounds array if there
  // are at least 2 rounds.
  function priorFromRounds(p) {
    if (!opts.priorPayload && p && Array.isArray(p.rounds) && p.rounds.length >= 2) {
      const prevRound = p.rounds[p.rounds.length - 2];
      // Shape it as a payload-lookalike with the *_claims keys.
      return {
        cfo_claims: prevRound.cfo_claims || [],
        cos_claims: prevRound.cos_claims || [],
        ceo_claims: prevRound.ceo_claims || [],
      };
    }
    return opts.priorPayload || null;
  }
  rerender(approval, priorFromRounds(payload));

  // SSE wiring for the live cost meters. We only subscribe when the
  // host page has an SSE endpoint mounted (Tauri / browser).
  let sse = null;
  if (typeof EventSource !== "undefined") {
    try {
      sse = connectSSE(opts.sseUrl || "/events", {
        onEvent: ({ type, data }) => {
          if (type !== "llm.spend") return;
          board.onSpend(data || {});
          for (const role of ROLES) {
            const col = colsByRole[role.key];
            const costRow = col.querySelector(".fr-col-cost");
            if (costRow) board.meters.get(role.key).renderInto(costRow);
          }
          total.textContent =
            `TOTAL THIS REVIEW: ${formatUsd(board.totalCost())}` +
            (board.lastBalance() != null
              ? ` · LEDGER NOW ${formatUsd(board.lastBalance())}`
              : "");
        },
      });
    } catch (_) {
      sse = null;
    }
  }

  function renderProposalCard(bundle) {
    if (!bundle) {
      proposalCard.hidden = true;
      return;
    }
    const f = bundle.founder || {};
    const p = bundle.proposal;
    if (!p) {
      // No team_proposal recorded yet (e.g. heuristic-only run, or
      // backend skipped it). Don't render the card — the columns are
      // the source of truth in that case.
      proposalCard.hidden = true;
      return;
    }
    const rows = [];
    const fmtUsdLocal = (n) =>
      n == null
        ? "--"
        : "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
    const fmtDeadline = (s) =>
      s ? String(s).slice(0, 10) : "--";
    const diffNote = (yours, theirs) => {
      if (yours == null || theirs == null || yours === theirs) return "";
      const delta = theirs - yours;
      const pct = yours ? Math.round((delta / yours) * 100) : null;
      const sign = delta > 0 ? "+" : "";
      return ` (${sign}${fmtUsdLocal(delta)}${pct != null ? `, ${sign}${pct}%` : ""})`;
    };

    function addRow(label, yours, theirs, formatter, diffFn) {
      const sameAsYours = yours === theirs || theirs == null;
      const theirsLabel = sameAsYours
        ? "unchanged"
        : `${formatter(theirs)}${diffFn ? diffFn(yours, theirs) : ""}`;
      rows.push(`
        <tr>
          <td class="fr-proposal-label">${label}</td>
          <td class="fr-proposal-yours">${formatter(yours)}</td>
          <td class="fr-proposal-theirs">${theirsLabel}</td>
        </tr>
      `);
    }

    addRow("revenue", f.revenue_target, p.revenue_target, fmtUsdLocal, diffNote);
    if (f.customer_target != null || p.customer_target != null) {
      addRow(
        "customers",
        f.customer_target,
        p.customer_target,
        (n) => (n == null ? "--" : String(n)),
        null,
      );
    }
    addRow("deadline", f.deadline, p.deadline, fmtDeadline, null);

    proposalCard.innerHTML = `
      <div class="fr-proposal-head">// IF YOU ADOPT — team's numbers vs yours</div>
      <table class="fr-proposal-table">
        <thead>
          <tr>
            <th></th>
            <th class="fr-proposal-yours">yours</th>
            <th class="fr-proposal-theirs">team proposes</th>
          </tr>
        </thead>
        <tbody>${rows.join("")}</tbody>
      </table>
    `;
    proposalCard.hidden = false;
  }

  return {
    rerender,
    setTargetsBundle: renderProposalCard,
    destroy: () => {
      if (sse && sse.close) sse.close();
      host.innerHTML = "";
    },
  };
}

// Named exports for tests / future reuse.
export { TeamCostBoard, CostMeter };
