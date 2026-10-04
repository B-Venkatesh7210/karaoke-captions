import { buildLines } from "./layout.js";

const LAYOUT_KEYS = ["max_words", "max_chars", "pause_break", "sentence_break", "line_hold"];
const HISTORY_LIMIT = 150;

/** Shared editor state with a tiny event bus. */
class State extends EventTarget {
  project = null;
  words = [];
  style = {};
  defaults = {};
  preview = { background: "dark", template_id: null };
  lines = [];
  selected = -1;
  fonts = { ready: false, families: [] };
  templates = [];
  #undo = [];
  #redo = [];

  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  on(type, fn) {
    this.addEventListener(type, (e) => fn(e.detail));
  }

  load(project) {
    this.project = project;
    this.words = (project.words || []).map((w) => ({ ...w }));
    this.style = { ...this.defaults, ...project.style };
    this.preview = { ...this.preview, ...(project.preview || {}) };
    this.selected = -1;
    this.#undo = [];
    this.#redo = [];
    this.relayout();
    this.emit("words", { reason: "load" });
    this.emit("style", { keys: Object.keys(this.style) });
  }

  relayout() {
    this.lines = buildLines(this.words, this.style);
  }

  // ----- words -----

  snapshot() {
    this.#undo.push(JSON.stringify(this.words));
    if (this.#undo.length > HISTORY_LIMIT) this.#undo.shift();
    this.#redo = [];
  }

  /** Apply a mutation to the words with undo support. */
  editWords(mutate, { select } = {}) {
    this.snapshot();
    mutate(this.words);
    this.words = this.words.filter((w) => w.text.trim());
    this.words.sort((a, b) => a.start - b.start);
    if (select !== undefined) this.selected = select;
    this.selected = Math.min(this.selected, this.words.length - 1);
    this.relayout();
    this.emit("words", { reason: "edit" });
    this.emit("dirty");
  }

  get canUndo() { return this.#undo.length > 0; }
  get canRedo() { return this.#redo.length > 0; }

  undo() {
    if (!this.#undo.length) return;
    this.#redo.push(JSON.stringify(this.words));
    this.words = JSON.parse(this.#undo.pop());
    this.selected = Math.min(this.selected, this.words.length - 1);
    this.relayout();
    this.emit("words", { reason: "undo" });
    this.emit("dirty");
  }

  redo() {
    if (!this.#redo.length) return;
    this.#undo.push(JSON.stringify(this.words));
    this.words = JSON.parse(this.#redo.pop());
    this.selected = Math.min(this.selected, this.words.length - 1);
    this.relayout();
    this.emit("words", { reason: "redo" });
    this.emit("dirty");
  }

  select(index) {
    this.selected = index;
    this.emit("select", index);
  }

  // ----- style -----

  setStyle(patch) {
    const keys = Object.keys(patch).filter((k) => this.style[k] !== patch[k]);
    if (!keys.length) return;
    Object.assign(this.style, patch);
    if (keys.some((k) => LAYOUT_KEYS.includes(k))) {
      this.relayout();
      this.emit("words", { reason: "layout" });
    }
    this.emit("style", { keys });
    this.emit("dirty");
  }

  setPreview(patch) {
    Object.assign(this.preview, patch);
    this.emit("preview", this.preview);
    this.emit("dirty");
  }

  family(name) {
    const key = (name || "").toLowerCase();
    return this.fonts.families.find((f) => f.name.toLowerCase() === key) || null;
  }
}

export const state = new State();
