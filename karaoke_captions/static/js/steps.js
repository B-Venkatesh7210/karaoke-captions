import { el, fmtTime } from "./util.js";

const ICONS = { done: "✓", error: "!", cancelled: "–", skipped: "–" };
const STATUS_TEXT = { skipped: "Skipped", cancelled: "Cancelled", error: "Failed", pending: "" };

/** Render a list of job steps, updating rows in place so bars animate between polls. */
export function renderSteps(container, steps) {
  const rows = container._rows || (container._rows = new Map());
  const visible = steps.filter((s) => !(s.optional && (s.status === "skipped" || s.status === "pending")));
  const ids = new Set(visible.map((s) => s.id));
  for (const [id, row] of rows) {
    if (!ids.has(id)) {
      row.root.remove();
      rows.delete(id);
    }
  }
  visible.forEach((step, index) => {
    let row = rows.get(step.id);
    if (!row) {
      row = buildRow();
      rows.set(step.id, row);
    }
    if (container.children[index] !== row.root) container.insertBefore(row.root, container.children[index] || null);
    updateRow(row, step);
  });
}

export function clearSteps(container) {
  container.replaceChildren();
  container._rows = null;
}

function buildRow() {
  const row = {
    icon: el("span", { class: "step-icon" }),
    label: el("span", { class: "step-label" }),
    pct: el("span", { class: "step-pct" }),
    fill: el("div"),
    detail: el("span", { class: "step-detail" }),
    time: el("span", { class: "step-time" }),
    hint: el("div", { class: "step-hint" }),
  };
  row.bar = el("div", { class: "progress" }, row.fill);
  row.root = el("div", { class: "step" },
    row.icon,
    el("div", { class: "step-body" },
      el("div", { class: "step-head" }, row.label, row.pct),
      row.bar,
      el("div", { class: "step-meta" }, row.detail, row.time),
      row.hint));
  return row;
}

function updateRow(row, step) {
  const { status } = step;
  row.root.className = `step is-${status}${step.estimated ? " is-estimated" : ""}${step.stalled ? " is-stalled" : ""}`;
  row.icon.textContent = ICONS[status] || "";
  row.label.textContent = step.label;
  row.pct.textContent = status in STATUS_TEXT ? STATUS_TEXT[status] : `${step.estimated ? "~" : ""}${Math.round(step.progress * 100)}%`;
  row.pct.title = step.estimated ? "Estimated from how long this took on previous runs" : "";
  row.bar.hidden = status === "skipped";
  row.fill.style.width = `${(step.progress * 100).toFixed(1)}%`;
  row.detail.textContent = step.detail || "";
  row.time.textContent = step.elapsed != null && status !== "pending" ? fmtTime(step.elapsed, false) : "";
  row.hint.textContent = step.stalled ? step.hint || "This is taking much longer than expected." : "";
  row.hint.hidden = !step.stalled;
}
