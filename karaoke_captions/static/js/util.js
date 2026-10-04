export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function debounce(fn, wait) {
  let timer;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  wrapped.flush = (...args) => {
    clearTimeout(timer);
    return fn(...args);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

export function fmtTime(seconds, withCs = true) {
  if (!Number.isFinite(seconds)) seconds = 0;
  seconds = Math.max(0, seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const ss = withCs ? s.toFixed(2).padStart(5, "0") : String(Math.floor(s)).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function hexToRgba(hex, opacity = 100) {
  const clean = (hex || "#ffffff").replace("#", "");
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(100, opacity)) / 100})`;
}

export function normalizeHex(value) {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(value || "").trim());
  if (match) return `#${match[1].toUpperCase()}`;
  const short = /^#?([0-9a-f]{3})$/i.exec(String(value || "").trim());
  if (short) return `#${short[1].split("").map((c) => c + c).join("").toUpperCase()}`;
  return null;
}

export function toast(message, type = "") {
  const node = el("div", { class: `toast ${type}` }, message);
  document.getElementById("toasts").append(node);
  setTimeout(() => node.remove(), type === "error" ? 6000 : 3000);
}

export function downloadBlob(filename, content, type = "application/json") {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function isTyping(event) {
  const t = event.target;
  return t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement || t?.isContentEditable;
}

export function askText(label, value = "") {
  const dialog = document.getElementById("promptDialog");
  const input = document.getElementById("promptInput");
  document.getElementById("promptLabel").textContent = label;
  input.value = value;
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok" ? input.value.trim() : null), { once: true });
    dialog.showModal();
    input.select();
  });
}
