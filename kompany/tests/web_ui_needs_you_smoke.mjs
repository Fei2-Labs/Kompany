// NEEDS YOU composition smoke: merge + severity sort + blocked extraction.
// Pure module, imported straight from the source tree (no DOM needed).
import assert from "node:assert/strict";
import { buildNeedsYouItems, blockedTasksFromProject, healthSeverity } from
  "../src/kompany/web_ui/static/modules/ui/needs_you.js";

const pending = [
  { id: "a1", severity: "medium", created_at: "2026-09-04 10:00:00" },
  { id: "a2", severity: "high", created_at: "2026-09-04 12:00:00" },
  { id: "a3", severity: "low", created_at: "2026-09-04 09:00:00" },
];
const health = [
  { id: "he_1", kind: "runway_alert", created_at: "2026-09-04 11:00:00" },
  { id: "he_2", kind: "llm_retry", created_at: "2026-09-04 08:00:00" },
];
const project = {
  id: "p1", name: "Launch", tasks: [
    { id: "t1", status: "blocked", title: "Send outreach", agent: "cro",
      result: { founder_action: "connect an email account in Settings" }, updated_at: "2026-09-04 07:00:00" },
    { id: "t2", status: "completed", title: "Draft copy" },
  ],
};
const blocked = blockedTasksFromProject(project);
assert.equal(blocked.length, 1);
assert.equal(blocked[0].founder_action, "connect an email account in Settings");
assert.equal(blocked[0].project_name, "Launch");
assert.equal(blocked[0].assigned_agent, "cro");

const items = buildNeedsYouItems({ pending, health, blocked });
assert.equal(items.length, 6);
// high first (blocked t1 07:00 < runway 11:00 < approval a2 12:00), then medium, then low
assert.deepEqual(items.map((i) => `${i.kind}:${i.row.id || i.row.task_id}`), [
  "blocked:t1", "health:he_1", "approval:a2", "health:he_2", "approval:a1", "approval:a3",
]);
assert.equal(healthSeverity("stranded_task"), "high");
assert.equal(healthSeverity("unknown_kind"), "medium");
assert.deepEqual(buildNeedsYouItems(), []);
console.log("needs-you smoke: ok");
