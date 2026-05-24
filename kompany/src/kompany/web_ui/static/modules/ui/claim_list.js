// Claim list renderer — evidence-traced debate task (05-19).
//
// Renders ``list[Claim]`` payloads (as produced by Engine debate /
// feasibility review / CEO decision) into a per-line card view. Each
// claim is one row carrying:
//
//   * a left-edge marker (``▸`` if at least one Source is non-inferred,
//     ``⚠`` if the claim is inferred-only or has empty evidence),
//   * the claim text,
//   * an evidence chip ``[N sources]`` at the row tail; clicking it
//     toggles a collapsed source list inline.
//
// Inferred / empty-evidence rows get an amber border so the founder
// spots unsourced claims without hovering.
//
// Legacy fallback: when the payload has ``claims=[]`` but an ``analysis``
// (or ``consensus_position`` / ``rationale``) string is present, the
// caller passes that string as ``legacyText`` and the component renders
// it as a single ``(legacy view)`` block with the inferred marker.
//
// No build step — vanilla ES module. Consumers ``import`` this and call
// ``renderClaimList(container, claims, {legacyText})``.

const INFERRED = "inferred";

function isInferredOnly(claim) {
  const ev = Array.isArray(claim.evidence) ? claim.evidence : [];
  if (ev.length === 0) return true;
  return ev.every((s) => (s.source_type || "").toLowerCase() === INFERRED);
}

function hasAnyInferred(claim) {
  const ev = Array.isArray(claim.evidence) ? claim.evidence : [];
  if (ev.length === 0) return true;
  return ev.some((s) => (s.source_type || "").toLowerCase() === INFERRED);
}

function countSources(claim) {
  const ev = Array.isArray(claim.evidence) ? claim.evidence : [];
  return {
    total: ev.length,
    inferred: ev.filter((s) => (s.source_type || "").toLowerCase() === INFERRED).length,
  };
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sourceLabel(src) {
  // Prefer a stable type+ref label; fall back to type alone when ref
  // is empty (typical for inferred sources).
  const t = escapeHtml(src.source_type || "unknown");
  const ref = escapeHtml(src.source_ref || "");
  const supported = escapeHtml(src.claim_supported || "");
  const head = ref ? `${t}: ${ref}` : t;
  return supported ? `${head} (${supported})` : head;
}

function makeEvidenceChip(claim) {
  const { total, inferred } = countSources(claim);
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "claim-evidence-chip";
  if (total === 0) {
    // Zero sources is treated as inferred-only by isInferredOnly,
    // so the row's amber treatment already signals the warning.
    chip.classList.add("claim-evidence-chip-inferred");
    chip.textContent = "[0 sources]";
  } else if (inferred === total) {
    chip.classList.add("claim-evidence-chip-inferred");
    chip.textContent = `[${total} inferred]`;
  } else if (inferred > 0) {
    chip.classList.add("claim-evidence-chip-mixed");
    chip.textContent = `[${total} src · ${inferred} inferred]`;
  } else {
    chip.textContent = `[${total} source${total === 1 ? "" : "s"}]`;
  }
  return chip;
}

function makeEvidencePanel(claim) {
  const ev = Array.isArray(claim.evidence) ? claim.evidence : [];
  const panel = document.createElement("ul");
  panel.className = "claim-evidence-panel";
  panel.hidden = true;
  if (ev.length === 0) {
    const li = document.createElement("li");
    li.className = "claim-evidence-empty";
    li.textContent = "(no evidence cited — inferred claim)";
    panel.appendChild(li);
    return panel;
  }
  for (const src of ev) {
    const li = document.createElement("li");
    li.className = "claim-evidence-source";
    // Mark each inferred source visibly so the founder can see why a
    // row got the amber treatment even when other sources exist.
    if ((src.source_type || "").toLowerCase() === INFERRED) {
      li.classList.add("claim-evidence-source-inferred");
    }
    li.innerHTML = sourceLabel(src);
    panel.appendChild(li);
  }
  return panel;
}

function makeClaimRow(claim) {
  const row = document.createElement("div");
  row.className = "claim-row";
  const inferred = isInferredOnly(claim);
  const mixed = !inferred && hasAnyInferred(claim);
  if (inferred) row.classList.add("claim-row-inferred");
  else if (mixed) row.classList.add("claim-row-mixed");

  const marker = document.createElement("span");
  marker.className = "claim-marker";
  marker.textContent = inferred ? "⚠" : mixed ? "◌" : "▸";
  marker.setAttribute(
    "aria-label",
    inferred
      ? "inferred or unsourced claim"
      : mixed
        ? "claim partially backed by inferred sources"
        : "sourced claim",
  );

  const text = document.createElement("span");
  text.className = "claim-text";
  text.textContent = claim.text || "";

  const chip = makeEvidenceChip(claim);
  const panel = makeEvidencePanel(claim);

  chip.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    chip.classList.toggle("claim-evidence-chip-open", !panel.hidden);
  });

  row.appendChild(marker);
  row.appendChild(text);
  row.appendChild(chip);
  row.appendChild(panel);
  return row;
}

function makeLegacyRow(legacyText) {
  const row = document.createElement("div");
  row.className = "claim-row claim-row-inferred claim-row-legacy";

  const marker = document.createElement("span");
  marker.className = "claim-marker";
  marker.textContent = "⚠";

  const text = document.createElement("span");
  text.className = "claim-text";
  text.textContent = legacyText;

  const tag = document.createElement("span");
  tag.className = "claim-legacy-tag";
  tag.textContent = "(legacy view)";

  row.appendChild(marker);
  row.appendChild(text);
  row.appendChild(tag);
  return row;
}

function makeEmptyRow(emptyText) {
  const row = document.createElement("div");
  row.className = "claim-row claim-row-empty";
  row.textContent = emptyText || "(no claims)";
  return row;
}

/**
 * Render a list of Claim dicts into ``container``.
 *
 * @param {HTMLElement} container — element to populate (will be wiped).
 * @param {Array<{text:string, evidence:Array}>} claims — Claim payloads.
 * @param {{legacyText?:string, emptyText?:string}} [opts]
 *   ``legacyText`` is rendered as a single ``(legacy view)`` row when
 *   ``claims`` is empty. ``emptyText`` is shown when both ``claims``
 *   and ``legacyText`` are empty.
 */
export function renderClaimList(container, claims, opts = {}) {
  if (!container) return;
  container.innerHTML = "";
  const list = Array.isArray(claims) ? claims : [];
  if (list.length === 0) {
    const legacy = (opts.legacyText || "").trim();
    if (legacy) {
      container.appendChild(makeLegacyRow(legacy));
      return;
    }
    container.appendChild(makeEmptyRow(opts.emptyText));
    return;
  }
  for (const claim of list) {
    container.appendChild(makeClaimRow(claim));
  }
}

// Exported helpers — kept named so future panels can reuse the inferred
// check / source label without re-importing the whole render path.
export { isInferredOnly, sourceLabel };
