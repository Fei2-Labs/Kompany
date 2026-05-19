// Thin fetch wrappers around the Kompany REST API. All methods return
// JSON or throw on non-2xx.

async function getJSON(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

async function postJSON(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  inbox: () => getJSON("/inbox"),
  episodes: () => getJSON("/episodes"),
  agentsStatus: () => getJSON("/agents/status"),
  status: () => getJSON("/status"),
  episode: (id) => getJSON(`/episodes/${encodeURIComponent(id)}`),
  directive: (text) => postJSON("/directive", { text }),
  approve: (id, comment) => postJSON(`/approvals/${encodeURIComponent(id)}/approve`, { comment: comment || "" }),
  reject: (id, reason, comment) => postJSON(`/approvals/${encodeURIComponent(id)}/reject`, { reason: reason || "", comment: comment || "" }),
  revise: (id, counter, comment) => postJSON(`/approvals/${encodeURIComponent(id)}/revise`, { counter, comment: comment || "" }),
  snooze: (id, minutes, comment) => postJSON(`/approvals/${encodeURIComponent(id)}/snooze`, { minutes, comment: comment || "" }),
  cancel: (id, reason, comment) => postJSON(`/approvals/${encodeURIComponent(id)}/cancel`, { reason: reason || "", comment: comment || "" }),
  comment: (id, body) => postJSON(`/approvals/${encodeURIComponent(id)}/comment`, { body, by_type: "user" }),
};
