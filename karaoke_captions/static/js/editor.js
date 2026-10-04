import { nearestLine } from "./layout.js";
import { state } from "./state.js";
import { $, el, fmtTime, isTyping, toast } from "./util.js";

const LOW_CONFIDENCE = 0.5;
const NUDGE = 0.05;

const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Transcript editor: lines of word chips, an inspector, find/replace and review. */
export class Editor {
  constructor(player) {
    this.player = player;
    this.linesEl = $("#lines");
    this.inspector = $("#inspector");
    this.findInput = $("#findInput");
    this.replaceInput = $("#replaceInput");
    this.activeIdx = -1;
    this.currentLine = -1;
    this.editing = -1;
    this.matches = [];

    this.linesEl.addEventListener("click", (e) => this.onClick(e));
    this.linesEl.addEventListener("dblclick", (e) => {
      const chip = e.target.closest(".chip");
      if (chip) this.startEdit(Number(chip.dataset.i));
    });
    this.findInput.addEventListener("input", () => this.updateMatches());
    this.findInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this.nextMatch(e.shiftKey ? -1 : 1); }
      if (e.key === "Escape") { this.findInput.value = ""; this.updateMatches(); this.findInput.blur(); }
    });
    this.replaceInput.addEventListener("keydown", (e) => e.key === "Enter" && (e.preventDefault(), this.replaceAll()));
    $("#btnReplace").addEventListener("click", () => this.replaceAll());
    $("#btnUndo").addEventListener("click", () => state.undo());
    $("#btnRedo").addEventListener("click", () => state.redo());
    $("#btnReview").addEventListener("click", () => this.nextLowConfidence());
    document.addEventListener("keydown", (e) => this.onKey(e));

    state.on("words", () => this.renderAll());
    state.on("select", () => this.renderSelection());
  }

  get followPlayback() {
    return $("#followToggle").checked;
  }

  // ---------- rendering ----------

  renderAll() {
    const { words, lines } = state;
    const html = [];
    lines.forEach((line, li) => {
      html.push(`<div class="line" data-line="${li}"><button class="line-time" data-t="${line.start}" tabindex="-1">${fmtTime(line.start, false)}</button><div class="line-words">`);
      for (let i = line.from; i <= line.to; i++) {
        const w = words[i];
        const cls = ["chip"];
        if (w.prob !== undefined && w.prob !== null && w.prob < LOW_CONFIDENCE) cls.push("low");
        if (w.break_after) cls.push("brk");
        html.push(`<span class="${cls.join(" ")}" data-i="${i}">${escapeHtml(w.text)}</span>`);
      }
      html.push("</div></div>");
    });
    this.linesEl.innerHTML = html.join("") || `<div class="line"><span class="muted">No words yet.</span></div>`;
    this.activeIdx = -1;
    this.currentLine = -1;
    this.updateMatches(false);
    this.renderSelection();
    this.renderMeta();
  }

  renderMeta() {
    const low = state.words.filter((w) => w.prob != null && w.prob < LOW_CONFIDENCE).length;
    $("#editorMeta").textContent = `${state.words.length.toLocaleString()} words · ${state.lines.length.toLocaleString()} lines`;
    $("#btnReview").textContent = low ? `⚠ ${low} to review` : "";
    $("#btnUndo").disabled = !state.canUndo;
    $("#btnRedo").disabled = !state.canRedo;
  }

  chip(i) {
    return this.linesEl.querySelector(`.chip[data-i="${i}"]`);
  }

  renderSelection() {
    this.linesEl.querySelectorAll(".chip.selected").forEach((c) => c.classList.remove("selected"));
    const i = state.selected;
    const chip = i >= 0 ? this.chip(i) : null;
    chip?.classList.add("selected");
    this.renderInspector();
  }

  scrollToChip(i) {
    const chip = this.chip(i);
    if (!chip) return;
    const box = this.linesEl.getBoundingClientRect();
    const r = chip.getBoundingClientRect();
    if (r.top < box.top + 30 || r.bottom > box.bottom - 30) {
      this.linesEl.scrollTop += r.top - box.top - box.height / 2 + r.height / 2;
    }
  }

  /** Called every frame with the current playback time. */
  tick(t, playing) {
    let active = -1;
    const li = nearestLine(state.lines, t);
    if (li >= 0) {
      const line = state.lines[li];
      if (t >= line.start && t < line.end) {
        for (let i = line.from; i <= line.to; i++) {
          if (state.words[i].start <= t) active = i;
        }
      }
    }
    if (active !== this.activeIdx) {
      if (this.activeIdx >= 0) this.chip(this.activeIdx)?.classList.remove("active");
      if (active >= 0) this.chip(active)?.classList.add("active");
      this.activeIdx = active;
    }
    if (li !== this.currentLine) {
      this.linesEl.querySelector(".line.current")?.classList.remove("current");
      const row = this.linesEl.querySelector(`.line[data-line="${li}"]`);
      row?.classList.add("current");
      this.currentLine = li;
      if (playing && this.followPlayback && row && this.editing < 0) {
        const box = this.linesEl.getBoundingClientRect();
        const r = row.getBoundingClientRect();
        if (r.top < box.top || r.bottom > box.bottom - 40) {
          this.linesEl.scrollTop += r.top - box.top - box.height * 0.3;
        }
      }
    }
  }

  // ---------- inspector ----------

  renderInspector() {
    const i = state.selected;
    const w = state.words[i];
    if (!w) {
      this.inspector.replaceChildren(el("span", { class: "empty" }, "Select a word to edit its text and timing. Click a timestamp to jump to that line."));
      return;
    }

    const text = el("input", { class: "word-text", value: w.text, spellcheck: "true", "aria-label": "Word text" });
    text.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { this.commitText(i, text.value); text.blur(); }
      if (e.key === "Escape") { text.value = w.text; text.blur(); }
    });
    text.addEventListener("change", () => this.commitText(i, text.value));

    const timeField = (label, key) => {
      const input = el("input", { type: "number", step: "0.01", min: "0", value: w[key].toFixed(2) });
      input.addEventListener("change", () => this.setTime(i, key, Number(input.value)));
      return el("div", { class: "time-field" },
        el("label", {}, label),
        el("button", { class: "btn small nudge", title: `−${NUDGE}s`, onclick: () => this.setTime(i, key, w[key] - NUDGE) }, "−"),
        input,
        el("button", { class: "btn small nudge", title: `+${NUDGE}s`, onclick: () => this.setTime(i, key, w[key] + NUDGE) }, "+"),
      );
    };

    const conf = w.prob != null && w.prob < LOW_CONFIDENCE
      ? el("span", { class: "conf", title: "Whisper's confidence for this word" }, `${Math.round(w.prob * 100)}% sure`)
      : null;

    this.inspector.replaceChildren(
      text,
      timeField("Start", "start"),
      timeField("End", "end"),
      el("button", { class: "btn small", title: "Play this word", onclick: () => this.playWord(i) }, "▶ Play"),
      el("button", { class: `btn small ${w.break_after ? "active" : ""}`, title: "Start a new caption line after this word (B)", onclick: () => this.toggleBreak(i) }, "↵ Break"),
      el("button", { class: "btn small", title: "Merge with the next word (M)", onclick: () => this.merge(i), disabled: i >= state.words.length - 1 }, "Merge →"),
      el("button", { class: "btn small", title: "Insert a new word after this one", onclick: () => this.insertAfter(i) }, "+ Word"),
      el("button", { class: "btn small ghost danger", title: "Delete (Del)", onclick: () => this.remove(i) }, "Delete"),
      el("span", { class: "spacer" }),
      ...(conf ? [conf] : []),
    );
  }

  // ---------- actions ----------

  onClick(e) {
    const time = e.target.closest(".line-time");
    if (time) {
      this.player.seek(Number(time.dataset.t));
      return;
    }
    const chip = e.target.closest(".chip");
    if (!chip || chip.classList.contains("editing")) return;
    const i = Number(chip.dataset.i);
    state.select(i);
    this.player.seek(state.words[i].start);
  }

  select(i, { seek = true, scroll = true } = {}) {
    if (i < 0 || i >= state.words.length) return;
    state.select(i);
    if (seek) this.player.seek(state.words[i].start);
    if (scroll) this.scrollToChip(i);
  }

  startEdit(i) {
    const chip = this.chip(i);
    if (!chip) return;
    state.select(i);
    this.editing = i;
    const original = state.words[i].text;
    chip.classList.add("editing");
    chip.contentEditable = "plaintext-only";
    chip.focus();
    document.getSelection().selectAllChildren(chip);

    let done = false;
    const finish = (save, move = 0) => {
      if (done) return;
      done = true;
      chip.contentEditable = "false";
      chip.classList.remove("editing");
      this.editing = -1;
      const value = chip.textContent;
      if (save) {
        const count = this.commitText(i, value);
        if (move) {
          const target = move > 0 ? i + Math.max(1, count) : i - 1;
          if (target >= 0 && target < state.words.length) requestAnimationFrame(() => this.startEdit(target));
        }
      } else {
        chip.textContent = original;
      }
    };
    chip.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
      else if (e.key === "Tab") { e.preventDefault(); finish(true, e.shiftKey ? -1 : 1); }
    });
    chip.addEventListener("blur", () => finish(true), { once: true });
  }

  /** Update a word's text. Spaces split it into several timed words. Returns the word count. */
  commitText(i, value) {
    const w = state.words[i];
    if (!w) return 0;
    const tokens = value.trim().split(/\s+/).filter(Boolean);
    if (tokens.length === 1 && tokens[0] === w.text) {
      if (w.prob != null) state.editWords((ws) => { delete ws[i].prob; }, { select: i });
      return 1;
    }
    if (!tokens.length) {
      this.remove(i);
      return 0;
    }
    state.editWords((ws) => {
      const span = ws[i].end - ws[i].start;
      const total = tokens.reduce((n, t) => n + t.length, 0);
      let cursor = ws[i].start;
      const pieces = tokens.map((t, k) => {
        const dur = span * (t.length / total);
        const piece = { text: t, start: +cursor.toFixed(3), end: +(cursor + dur).toFixed(3) };
        cursor += dur;
        if (k === tokens.length - 1 && ws[i].break_after) piece.break_after = true;
        return piece;
      });
      ws.splice(i, 1, ...pieces);
    }, { select: i });
    return tokens.length;
  }

  setTime(i, key, value) {
    if (!Number.isFinite(value)) return;
    const target = state.words[i];
    state.editWords(() => {
      target[key] = Math.max(0, +value.toFixed(3));
      if (target.end < target.start) {
        if (key === "start") target.end = target.start;
        else target.start = target.end;
      }
    });
    state.select(state.words.indexOf(target));
  }

  toggleBreak(i) {
    state.editWords((ws) => { ws[i].break_after = !ws[i].break_after; if (!ws[i].break_after) delete ws[i].break_after; }, { select: i });
  }

  merge(i) {
    if (i < 0 || i >= state.words.length - 1) return;
    state.editWords((ws) => {
      const a = ws[i], b = ws[i + 1];
      const merged = { text: `${a.text} ${b.text}`, start: a.start, end: Math.max(a.end, b.end) };
      if (b.break_after) merged.break_after = true;
      ws.splice(i, 2, merged);
    }, { select: i });
  }

  insertAfter(i) {
    const w = state.words[i];
    const next = state.words[i + 1];
    const start = w.end;
    const end = next ? Math.max(start + 0.05, Math.min(next.start, start + 0.4)) : start + 0.4;
    state.editWords((ws) => ws.splice(i + 1, 0, { text: "word", start: +start.toFixed(3), end: +end.toFixed(3) }), { select: i + 1 });
    requestAnimationFrame(() => this.startEdit(i + 1));
  }

  remove(i) {
    if (!state.words[i]) return;
    state.editWords((ws) => ws.splice(i, 1), { select: Math.min(i, state.words.length - 2) });
  }

  playWord(i) {
    const w = state.words[i];
    this.player.playRange(w.start, Math.max(w.end, w.start + 0.15));
  }

  // ---------- find / replace / review ----------

  updateMatches(jump = false) {
    const q = this.findInput.value.trim().toLowerCase();
    this.linesEl.querySelectorAll(".chip.match").forEach((c) => c.classList.remove("match"));
    this.matches = [];
    if (q) {
      state.words.forEach((w, i) => {
        if (w.text.toLowerCase().includes(q)) {
          this.matches.push(i);
          this.chip(i)?.classList.add("match");
        }
      });
    }
    $("#findCount").textContent = q ? `${this.matches.length}` : "";
    if (jump && this.matches.length) this.select(this.matches[0]);
  }

  nextMatch(dir) {
    if (!this.matches.length) return;
    const cur = state.selected;
    const next = dir > 0
      ? this.matches.find((i) => i > cur) ?? this.matches[0]
      : [...this.matches].reverse().find((i) => i < cur) ?? this.matches[this.matches.length - 1];
    this.select(next);
  }

  replaceAll() {
    const q = this.findInput.value.trim();
    if (!q) { this.findInput.focus(); return; }
    const replacement = this.replaceInput.value;
    const re = new RegExp(escapeRegExp(q), "gi");
    let count = 0;
    state.editWords((ws) => {
      for (const w of ws) {
        if (re.test(w.text)) {
          re.lastIndex = 0;
          w.text = w.text.replace(re, replacement).trim();
          delete w.prob;
          count++;
        }
        re.lastIndex = 0;
      }
    });
    toast(count ? `Replaced in ${count} word${count === 1 ? "" : "s"}` : "No matches");
  }

  nextLowConfidence() {
    const low = [];
    state.words.forEach((w, i) => w.prob != null && w.prob < LOW_CONFIDENCE && low.push(i));
    if (!low.length) return;
    const next = low.find((i) => i > state.selected) ?? low[0];
    this.select(next);
  }

  // ---------- keyboard ----------

  onKey(e) {
    if ($("#viewStudio").hidden || isTyping(e) || document.querySelector("dialog[open]")) return;
    const mod = e.ctrlKey || e.metaKey;
    const i = state.selected;
    if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? state.redo() : state.undo(); return; }
    if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); state.redo(); return; }
    if (mod && e.key.toLowerCase() === "f") { e.preventDefault(); this.findInput.focus(); this.findInput.select(); return; }
    if (mod || e.altKey) return;

    switch (e.key) {
      case " ":
        e.preventDefault();
        this.player.toggle();
        break;
      case "ArrowRight":
        e.preventDefault();
        this.select(Math.min(state.words.length - 1, i + 1));
        break;
      case "ArrowLeft":
        e.preventDefault();
        this.select(Math.max(0, i - 1));
        break;
      case "Enter":
      case "F2":
        if (i >= 0) { e.preventDefault(); this.startEdit(i); }
        break;
      case "Delete":
      case "Backspace":
        if (i >= 0) { e.preventDefault(); this.remove(i); }
        break;
      case "b":
      case "B":
        if (i >= 0) this.toggleBreak(i);
        break;
      case "m":
      case "M":
        if (i >= 0) this.merge(i);
        break;
      case "Escape":
        state.select(-1);
        break;
    }
  }
}
