// Header stats: cash, burn, rev, days, run, episode count, brand.
//
// Mission-targets task (05-19) added the ``rev`` and ``days`` slots so
// the cyberpunk header surfaces the agreed revenue target + remaining
// runway alongside cash on hand. Data flows from ``GET /targets``;
// failures degrade to ``--`` rather than blocking the header render.

function fmtCash(v) {
  const n = Number(v || 0);
  return `$${n.toFixed(2)}`;
}

function fmtBurn(v) {
  // burn isn't directly exposed today; estimate from total_ai_costs / hour
  // would require runtime data we don't have. Fall back to "--" so the
  // pixel doesn't lie.
  if (!v && v !== 0) return "$0/h";
  return `$${Number(v).toFixed(2)}/h`;
}

function fmtMoney(v) {
  const n = Number(v || 0);
  return `$${n.toFixed(0)}`;
}

// ``revenue_so_far`` isn't tracked as a first-class metric yet — we
// derive it from the running ``total_income`` until a richer revenue
// stream model lands. For now ``rev: $X / $Y`` shows current income
// against the agreed revenue target so the cyberpunk header still
// renders the right kind of progress bar.
function renderTargets(targetsPayload, status) {
  const revEl = document.getElementById("stat-rev");
  const daysEl = document.getElementById("stat-days");
  if (!targetsPayload) {
    if (revEl) revEl.textContent = "--";
    if (daysEl) daysEl.textContent = "--";
    return;
  }
  const auth = targetsPayload.authoritative || {};
  const revTarget = Number(auth.revenue_target || 0);
  // Prefer status.total_income (already a positive number in /status)
  // and fall back to balance for installs that pre-date that field.
  const revSoFar = Number(
    (status && (status.total_income != null ? status.total_income : status.balance)) || 0,
  );
  if (revEl) {
    if (revTarget > 0) {
      revEl.textContent = `${fmtMoney(revSoFar)} / ${fmtMoney(revTarget)}`;
    } else {
      revEl.textContent = "--";
    }
  }

  if (daysEl) {
    // Virtual time (model D): the founder's deadline gets translated
    // into a virtual-day budget at template-apply, and the team burns
    // 1 virtual day per completed task. Display the remaining /
    // budget so a paused Kompany doesn't lie about runway and a
    // productive team doesn't get penalised by clock drift.
    const budget = Number((status && status.virtual_days_budget) || 0);
    const remaining = Number((status && status.virtual_days_remaining) || 0);
    if (budget > 0) {
      daysEl.textContent = `${remaining}/${budget} vd`;
    } else {
      daysEl.textContent = "--";
    }
  }
}

let _targetsCache = null;
let _targetsLastFetch = 0;
const _TARGETS_TTL_MS = 30_000;

async function fetchTargets() {
  const now = Date.now();
  if (_targetsCache && now - _targetsLastFetch < _TARGETS_TTL_MS) {
    return _targetsCache;
  }
  try {
    const res = await fetch("/targets", {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return _targetsCache;
    _targetsCache = await res.json();
    _targetsLastFetch = now;
    return _targetsCache;
  } catch (_) {
    return _targetsCache;
  }
}

export function renderLedger(status) {
  const brand = document.getElementById("brand-name");
  const cash = document.getElementById("stat-cash");
  const burn = document.getElementById("stat-burn");
  const run = document.getElementById("stat-run");
  const eps = document.getElementById("stat-episodes");

  if (brand && status.company) brand.textContent = String(status.company).toUpperCase();
  if (cash) cash.textContent = fmtCash(status.balance);
  if (burn) burn.textContent = fmtBurn(status.burn_rate);
  if (run) run.textContent = status.run_id ? String(status.run_id).slice(0, 10) + "..." : "--";
  if (eps) eps.textContent = status.episode_count != null ? String(status.episode_count) : String(status.active_projects || 0);

  // Build staleness (#26): "abc1234" normally; "abc1234 +16" in warn colour
  // when the running engine predates the repo checkout.
  const build = document.getElementById("stat-build");
  const wrap = document.getElementById("stat-build-wrap");
  const b = status.build || {};
  if (build) {
    const commit = b.commit && b.commit !== "unknown" ? b.commit : (b.version || "--");
    build.textContent = b.stale ? `${commit} +${b.newer_commits}` : commit;
    build.style.color = b.stale ? "var(--warn, #d9a441)" : "";
    if (wrap) wrap.title = b.stale
      ? `running ${commit}, repo HEAD ${b.repo_head} — ${b.newer_commits} newer commit(s). ${b.hint || "Restart to pick them up."}`
      : `running build ${commit}${b.version ? " · v" + b.version : ""}`;
  }

  // Targets is async — render best-effort. Failures keep the existing
  // dashes in place rather than disturbing the header layout.
  fetchTargets().then((payload) => renderTargets(payload, status));
}
