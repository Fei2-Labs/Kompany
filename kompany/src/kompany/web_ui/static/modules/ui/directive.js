// Bottom directive input. Enter submits to the supplied handler.

export function initDirective(onSubmit) {
  const input = document.getElementById("directive-input");
  const status = document.getElementById("directive-status");
  if (!input) return;

  function setStatus(text, cls = "") {
    if (!status) return;
    status.textContent = text || "";
    status.classList.remove("busy", "err", "ok");
    if (cls) status.classList.add(cls);
  }

  input.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    const text = input.value.trim();
    if (!text) return;
    input.disabled = true;
    setStatus("executing...", "busy");
    try {
      await onSubmit(text);
      input.value = "";
      setStatus("done.", "ok");
    } catch (err) {
      setStatus(`error: ${err.message}`, "err");
    } finally {
      input.disabled = false;
      input.focus();
    }
  });

  input.focus();
}
