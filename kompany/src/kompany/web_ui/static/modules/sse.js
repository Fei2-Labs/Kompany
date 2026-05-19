// Thin EventSource wrapper. Browsers handle reconnect natively; we just
// surface open/error hooks and route every event through onEvent.
//
// The server emits every event as the default ``message`` event with the
// type encoded inside the JSON payload (``{ type, data }``). This sidesteps
// EventSource's inability to wildcard named event listeners.

export function connectSSE(url, { onOpen, onError, onEvent }) {
  const source = new EventSource(url);

  source.addEventListener("open", () => onOpen && onOpen());
  source.addEventListener("error", () => onError && onError());

  source.addEventListener("message", (e) => {
    if (!onEvent) return;
    let envelope = {};
    try {
      envelope = e.data ? JSON.parse(e.data) : {};
    } catch (_) {
      envelope = { raw: e.data };
    }
    onEvent({
      type: envelope.type || "message",
      data: envelope.data || {},
      id: e.lastEventId,
    });
  });

  return {
    close: () => source.close(),
    source,
  };
}
