// Live agent drawer store smoke: per-role ring buffer cap + targeted notify.
import assert from "node:assert/strict";
import { store } from "../src/kompany/web_ui/static/modules/store.js";

const seen = [];
store.subscribe("activity", (d) => seen.push(d.role));
for (let i = 0; i < 250; i++) store.pushActivity("CMO", { ts: i, kind: "text", text: `l${i}` });
store.pushActivity("cv", { ts: 1, kind: "spend", text: "$0.0100" });
assert.equal(store.state.activity.cmo.length, 200, "ring buffer caps at 200");
assert.equal(store.state.activity.cmo[0].text, "l50", "oldest evicted first");
assert.equal(store.state.activity.cv.length, 1);
assert.equal(seen.filter((r) => r === "cmo").length, 250, "one notify per push, lowercased role");
store.pushActivity("", { ts: 0 }); store.pushActivity("ceo", null);
assert.equal(Object.keys(store.state.activity).length, 2, "empty role / null line ignored");
console.log("activity store smoke: ok");
