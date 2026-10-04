import { cssFontFamily, ensureFont, fontScale } from "./fonts.js";
import { activeWord, lineAt } from "./layout.js";
import { state } from "./state.js";
import { $, hexToRgba } from "./util.js";

/**
 * HTML approximation of what libass draws, for instant feedback while
 * styling and playing. "Exact render" swaps in the real libass frame.
 */
export class Preview {
  constructor() {
    this.wrap = $("#stageWrap");
    this.stage = $("#stage");
    this.canvas = $("#canvas");
    this.layer = $("#captionLayer");
    this.exactImg = $("#exactImg");
    this.badge = $("#stageBadge");
    this.key = null;
    this.version = 0;
    this.fillWords = [];
    new ResizeObserver(() => this.fit()).observe(this.wrap);
  }

  fit() {
    const { width: W, height: H } = state.style;
    const box = this.wrap.getBoundingClientRect();
    if (!box.width || !box.height || !W || !H) return;
    const scale = Math.min(box.width / W, box.height / H);
    this.stage.style.width = `${Math.floor(W * scale)}px`;
    this.stage.style.height = `${Math.floor(H * scale)}px`;
    this.canvas.style.width = `${W}px`;
    this.canvas.style.height = `${H}px`;
    this.canvas.style.transform = `scale(${scale})`;
  }

  /** Recompute CSS from the style; call when the style changes. */
  applyStyle() {
    const s = state.style;
    ensureFont(s.font_family).then((ok) => ok && this.invalidate());
    const box = s.background === "box";
    const outline = s.background === "outline";
    const shadow = s.shadow_depth > 0 ? `${s.shadow_depth}px ${s.shadow_depth}px 0 ${hexToRgba(s.shadow_color, s.shadow_opacity)}` : "";

    const line = {
      left: `${s.margin_h}px`,
      right: `${s.margin_h}px`,
      fontFamily: cssFontFamily(s.font_family),
      fontSize: `${s.font_size * fontScale(s.font_family)}px`,
      lineHeight: `${s.font_size}px`,
      fontWeight: s.bold ? "700" : "400",
      fontStyle: s.italic ? "italic" : "normal",
      letterSpacing: `${s.letter_spacing}px`,
      textTransform: s.uppercase ? "uppercase" : "none",
      top: "", bottom: "", transform: "",
    };
    const edge = Math.max(0, s.margin_v - (box ? s.padding_y : 0));
    if (s.position === "bottom") line.bottom = `${edge}px`;
    else if (s.position === "top") line.top = `${edge}px`;
    else { line.top = "50%"; line.transform = "translateY(-50%)"; }

    const boxCss = box
      ? { background: hexToRgba(s.box_color, s.box_opacity), padding: `${s.padding_y}px ${s.padding_x}px`, boxShadow: shadow }
      : { background: "transparent", padding: "0", boxShadow: "" };

    const word = {
      webkitTextStroke: outline && s.outline_width > 0 ? `${s.outline_width * 2}px ${s.outline_color}` : "",
      paintOrder: "stroke fill",
      textShadow: !box ? shadow : "",
    };

    this.css = {
      line,
      box: boxCss,
      word,
      text: hexToRgba(s.text_color, s.text_opacity),
      highlight: s.highlight_color,
      scale: s.active_scale,
      mode: s.highlight_mode,
    };
    this.fit();
    this.invalidate();
  }

  invalidate() {
    this.version++;
    this.key = null;
  }

  render(t) {
    if (!this.css) this.applyStyle();
    const lines = state.lines;
    const words = state.words;
    const li = lineAt(lines, t);
    if (li < 0) {
      if (this.key !== "none") {
        this.layer.replaceChildren();
        this.key = "none";
      }
      return;
    }
    const line = lines[li];
    const active = activeWord(words, line, t);
    const fill = this.css.mode === "fill";
    const key = fill ? `${this.version}:${li}` : `${this.version}:${li}:${active}`;

    if (key !== this.key) {
      this.key = key;
      this.build(line, active);
    }
    if (fill) this.updateFill(line, t);
  }

  build(line, active) {
    const { css } = this;
    const words = state.words;
    const lineEl = document.createElement("div");
    lineEl.className = "cap-line";
    Object.assign(lineEl.style, css.line);
    const box = document.createElement("span");
    box.className = "cap-box";
    Object.assign(box.style, css.box);
    this.fillWords = [];

    for (let i = line.from; i <= line.to; i++) {
      if (i > line.from) box.append(" ");
      const span = document.createElement("span");
      span.className = "cap-w";
      span.textContent = words[i].text;
      Object.assign(span.style, css.word);
      if (css.mode === "fill") {
        span.classList.add("cap-fill");
        span.style.color = css.text;
        const sweep = document.createElement("span");
        sweep.className = "cap-sweep";
        sweep.textContent = words[i].text;
        Object.assign(sweep.style, css.word);
        sweep.style.textShadow = "none";
        sweep.style.color = css.highlight;
        span.append(sweep);
        this.fillWords.push({ sweep, i });
      } else {
        const lit = i === active || (css.mode === "progressive" && i < active);
        span.style.color = lit ? css.highlight : css.text;
        if (i === active && css.scale !== 100) span.style.fontSize = `${css.scale}%`;
      }
      box.append(span);
    }
    lineEl.append(box);
    this.layer.replaceChildren(lineEl);
  }

  updateFill(line, t) {
    const words = state.words;
    for (const { sweep, i } of this.fillWords) {
      const w = words[i];
      const end = i < line.to ? words[i + 1].start : w.end;
      const p = end > w.start ? Math.max(0, Math.min(1, (t - w.start) / (end - w.start))) : t >= w.start ? 1 : 0;
      sweep.style.clipPath = `inset(-50% ${(100 - p * 100).toFixed(1)}% -50% 0)`;
    }
  }

  showExact(url) {
    if (this.exactImg.dataset.url) URL.revokeObjectURL(this.exactImg.dataset.url);
    this.exactImg.src = url;
    this.exactImg.dataset.url = url;
    this.exactImg.hidden = false;
    this.layer.style.visibility = "hidden";
    this.setBadge("Exact render (libass)", "exact");
  }

  hideExact() {
    this.exactImg.hidden = true;
    this.layer.style.visibility = "";
    this.setBadge("Live preview");
  }

  setBadge(text, cls = "") {
    this.badge.textContent = text;
    this.badge.className = `stage-badge ${cls}`;
  }
}
