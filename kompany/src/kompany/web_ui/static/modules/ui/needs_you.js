// NEEDS YOU feed composition — pure functions, no DOM, no imports, so the
// node smoke test can import this file directly. Merges three sources the
// founder must act on (approvals, open watchdog health events, BLOCKED
// tasks) into one severity-sorted list. Inbox contract: only money /
// decision / connect-account asks ever appear here.

export const SEVERITY_RANK = { high: 0, medium: 1, low: 2 };

export function severityRank(sev) {
  const s = String(sev || "").toLowerCase();
  return SEVERITY_RANK[s] ?? 1;
}

// health_events has no severity column: kinds that stall the mission are
// high, everything else medium. Kept as a string match so new kinds land
// in a sane bucket without a code change.
export function healthSeverity(kind) {
  const k = String(kind || "").toLowerCase();
  if (/runway|stalled|stranded|timeout|blocked|exhaust|failed|drift/.test(k)) return "high";
  return "medium";
}

// pending: approval rows (status pending|snoozed) from /inbox
// health:  open rows from /health/events?status=open
// blocked: task rows with status=blocked from /projects/{id}
export function buildNeedsYouItems({ pending = [], health = [], blocked = [] } = {}) {
  const items = [
    ...pending.map((r) => ({
      kind: "approval",
      severity: String(r.severity || "high").toLowerCase(),
      created_at: r.created_at || "",
      row: r,
    })),
    ...health.map((ev) => ({
      kind: "health",
      severity: healthSeverity(ev.kind),
      created_at: ev.created_at || "",
      row: ev,
    })),
    ...blocked.map((t) => ({
      kind: "blocked",
      severity: "high",
      created_at: t.updated_at || t.created_at || "",
      row: t,
    })),
  ];
  items.sort((a, b) => {
    const d = severityRank(a.severity) - severityRank(b.severity);
    if (d !== 0) return d;
    return String(a.created_at).localeCompare(String(b.created_at));
  });
  return items;
}

// Blocked tasks out of a /projects/{id} payload. A BLOCKED task is a
// connect/approve ask (result.founder_action names it) — never labor.
export function blockedTasksFromProject(project) {
  const tasks = (project && project.tasks) || [];
  return tasks
    .filter((t) => String(t.status || "").toLowerCase() === "blocked")
    .map((t) => ({
      task_id: t.id,
      project_id: project.id || t.project_id,
      project_name: project.name || project.id || "",
      title: t.title || "(untitled)",
      assigned_agent: t.agent || t.assigned_agent || "",
      founder_action: (t.result && t.result.founder_action) || "needs a connection or approval",
      updated_at: t.updated_at || t.created_at || "",
    }));
}
