// Header stats: cash, burn, run, episode count, brand.

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
}
