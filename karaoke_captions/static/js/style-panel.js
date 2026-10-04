import { api } from "./api.js";
import { cssFontFamily, ensureFont } from "./fonts.js";
import { state } from "./state.js";
import { $, askText, downloadBlob, el, hexToRgba, normalizeHex, toast } from "./util.js";

const CANVAS_KEYS = ["width", "height", "fps"];
const SWATCHES = ["#FFFFFF", "#111111", "#A855F7", "#FACC15", "#22D3EE", "#34D399", "#F43F5E", "#FB923C", "#3B82F6", "#EC4899"];
const RESOLUTIONS = [
  ["1920x1080", "1920 × 1080 · 16:9 landscape"],
  ["1080x1920", "1080 × 1920 · 9:16 vertical"],
  ["1080x1080", "1080 × 1080 · 1:1 square"],
  ["1080x1350", "1080 × 1350 · 4:5 portrait"],
  ["1280x720", "1280 × 720 · 720p"],
  ["3840x2160", "3840 × 2160 · 4K"],
  ["custom", "Custom…"],
];

/** Every control returns { node, update } so the panel can sync without rebuilding. */
function range(key, label, { min, max, step = 1, unit = "", hint = "" }) {
  const slider = el("input", { type: "range", class: "range", min, max, step });
  const number = el("input", { type: "number", class: `num ${unit ? "has-unit" : ""}`, step });
  const set = (v) => {
    const n = Number(v);
    if (Number.isFinite(n)) state.setStyle({ [key]: n });
  };
  slider.addEventListener("input", () => set(slider.value));
  number.addEventListener("change", () => set(number.value));
  const node = el("div", { class: "ctl" },
    el("div", { class: "ctl-label" }, label, hint ? el("span", { class: "hint" }, hint) : null),
    el("div", { class: "ctl-row" }, slider, el("div", { class: "num-wrap" }, number, unit ? el("span", { class: "unit" }, unit) : null)),
  );
  return {
    node,
    update: () => {
      const v = state.style[key];
      slider.value = v;
      if (document.activeElement !== number) number.value = v;
    },
  };
}

function color(key, label, opacityKey) {
  const picker = el("input", { type: "color" });
  const hex = el("input", { class: "color-hex", maxlength: 7, spellcheck: "false" });
  picker.addEventListener("input", () => state.setStyle({ [key]: picker.value.toUpperCase() }));
  hex.addEventListener("change", () => {
    const v = normalizeHex(hex.value);
    if (v) state.setStyle({ [key]: v });
    else hex.value = state.style[key];
  });
  const swatches = el("div", { class: "swatches" },
    SWATCHES.map((c) => el("button", { style: { background: c }, title: c, onclick: () => state.setStyle({ [key]: c }) })));
  const swatchWrap = el("div", { class: "color-swatch" }, picker);
  const opacity = opacityKey ? range(opacityKey, "Opacity", { min: 0, max: 100, unit: "%" }) : null;
  const node = el("div", { class: "ctl" },
    el("div", { class: "ctl-label" }, label),
    el("div", { class: "color-ctl" }, swatchWrap, hex, swatches),
    opacity?.node,
  );
  return {
    node,
    update: () => {
      const v = state.style[key];
      picker.value = v.toLowerCase();
      if (document.activeElement !== hex) hex.value = v;
      opacity?.update();
      if (opacityKey) swatchWrap.style.background = hexToRgba(v, state.style[opacityKey]);
    },
  };
}

function segmented(key, label, options) {
  const buttons = options.map(([value, text, title]) =>
    el("button", { type: "button", title, onclick: () => state.setStyle({ [key]: value }) }, text));
  const node = el("div", { class: "ctl" }, label ? el("div", { class: "ctl-label" }, label) : null, el("div", { class: "seg" }, buttons));
  return {
    node,
    update: () => buttons.forEach((b, i) => b.classList.toggle("on", state.style[key] === options[i][0])),
  };
}

function toggles(items) {
  const buttons = items.map(([key, text, extraStyle]) =>
    el("button", { type: "button", style: extraStyle, onclick: () => state.setStyle({ [key]: !state.style[key] }) }, text));
  return {
    node: el("div", { class: "toggles" }, buttons),
    update: () => buttons.forEach((b, i) => b.classList.toggle("on", !!state.style[items[i][0]])),
  };
}

function pair(a, b) {
  return { node: el("div", { class: "ctl-pair" }, a.node, b.node), update: () => { a.update(); b.update(); } };
}

function visible(control, predicate) {
  return { node: control.node, update: () => { control.node.hidden = !predicate(state.style); if (!control.node.hidden) control.update(); } };
}

function fontPicker() {
  const input = el("input", { spellcheck: "false", placeholder: "Search fonts…", "aria-label": "Font family" });
  const menu = el("div", { class: "font-menu", hidden: true });
  const note = el("div", { class: "font-note" });
  const fileInput = el("input", { type: "file", accept: ".ttf,.otf,.ttc", hidden: true });
  let highlighted = 0;
  let options = [];

  const choose = (name) => {
    input.value = name;
    menu.hidden = true;
    state.setStyle({ font_family: name });
  };

  const renderMenu = () => {
    const q = input.value.trim().toLowerCase();
    const all = state.fonts.families;
    const list = (q && q !== state.style.font_family.toLowerCase() ? all.filter((f) => f.name.toLowerCase().includes(q)) : all).slice(0, 200);
    options = list.map((f) => f.name);
    highlighted = Math.max(0, options.indexOf(state.style.font_family));
    menu.replaceChildren(
      ...list.map((f, i) => {
        if (f.source === "uploaded") ensureFont(f.name);
        return el("div", {
          class: `font-opt ${i === highlighted ? "hl" : ""}`,
          style: { fontFamily: cssFontFamily(f.name) },
          onmousedown: (e) => { e.preventDefault(); choose(f.name); },
        }, f.name, f.source === "uploaded" ? el("span", { class: "tag" }, "uploaded") : null);
      }),
    );
    if (!list.length) {
      menu.append(el("div", { class: "font-empty" }, state.fonts.ready ? `No installed font matches. Press Enter to use “${input.value.trim()}” anyway.` : "Loading fonts…"));
    }
  };

  input.addEventListener("focus", () => { input.select(); menu.hidden = false; renderMenu(); });
  input.addEventListener("input", () => { menu.hidden = false; renderMenu(); });
  input.addEventListener("blur", () => setTimeout(() => { menu.hidden = true; input.value = state.style.font_family; }, 120));
  input.addEventListener("keydown", (e) => {
    const opts = [...menu.querySelectorAll(".font-opt")];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      highlighted = Math.max(0, Math.min(opts.length - 1, highlighted + (e.key === "ArrowDown" ? 1 : -1)));
      opts.forEach((o, i) => o.classList.toggle("hl", i === highlighted));
      opts[highlighted]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      const name = options[highlighted] || input.value.trim();
      if (name) choose(name);
      input.blur();
    } else if (e.key === "Escape") {
      input.blur();
    }
  });

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    fileInput.value = "";
    if (!file) return;
    try {
      const res = await api.uploadFont(file);
      const data = await api.fonts();
      state.fonts = data;
      state.emit("fonts");
      choose(res.family);
      toast(`Added font “${res.family}”`, "ok");
    } catch (err) {
      toast(err.message, "error");
    }
  });

  const node = el("div", { class: "ctl" },
    el("div", { class: "ctl-label" }, "Font family",
      el("button", { class: "btn small ghost", type: "button", onclick: () => fileInput.click(), title: "Add a .ttf or .otf file" }, "+ Upload font")),
    el("div", { class: "font-picker" }, input, menu),
    note,
    fileInput,
  );
  return {
    node,
    update: () => {
      if (document.activeElement !== input) input.value = state.style.font_family;
      input.style.fontFamily = cssFontFamily(state.style.font_family);
      const fam = state.family(state.style.font_family);
      if (!state.fonts.ready) note.textContent = "Scanning installed fonts…";
      else if (!fam) note.textContent = "This font isn't installed. The export will fall back to a default font.";
      else note.textContent = `${fam.faces.length} style${fam.faces.length === 1 ? "" : "s"} · ${fam.source === "uploaded" ? "uploaded" : "installed"}`;
      note.className = `font-note ${state.fonts.ready && !fam ? "warn" : ""}`;
    },
  };
}

function canvasControls() {
  const select = el("select", {}, RESOLUTIONS.map(([v, t]) => el("option", { value: v }, t)));
  const w = el("input", { type: "number", class: "num", min: 16, step: 2 });
  const h = el("input", { type: "number", class: "num", min: 16, step: 2 });
  const custom = el("div", { class: "ctl-row" }, w, el("span", { class: "muted" }, "×"), h);
  const fps = el("select", {}, [24, 25, 30, 50, 60].map((f) => el("option", { value: f }, `${f} fps`)));
  select.addEventListener("change", () => {
    if (select.value === "custom") { custom.hidden = false; return; }
    const [width, height] = select.value.split("x").map(Number);
    state.setStyle({ width, height });
  });
  const setCustom = () => state.setStyle({ width: Math.max(16, Number(w.value) || 1920), height: Math.max(16, Number(h.value) || 1080) });
  w.addEventListener("change", setCustom);
  h.addEventListener("change", setCustom);
  fps.addEventListener("change", () => state.setStyle({ fps: Number(fps.value) }));
  const node = el("div", { class: "ctl" },
    el("div", { class: "ctl-label" }, "Resolution"), select, custom,
    el("div", { class: "ctl-label" }, "Frame rate"), fps);
  return {
    node,
    update: () => {
      const key = `${state.style.width}x${state.style.height}`;
      const known = RESOLUTIONS.some(([v]) => v === key);
      if (select.value !== "custom" || known) select.value = known ? key : "custom";
      custom.hidden = select.value !== "custom";
      w.value = state.style.width;
      h.value = state.style.height;
      fps.value = String(state.style.fps);
    },
  };
}

function presetControls() {
  const grid = el("div", { class: "presets" });
  const importInput = el("input", { type: "file", accept: ".json,application/json", hidden: true });
  let presets = [];

  const apply = (style) => {
    const base = Object.fromEntries(Object.entries(state.defaults).filter(([k]) => !CANVAS_KEYS.includes(k)));
    const clean = Object.fromEntries(Object.entries(style).filter(([k]) => !CANVAS_KEYS.includes(k)));
    state.setStyle({ ...base, ...clean });
  };

  const card = (p) => {
    const s = { ...state.defaults, ...p.style };
    const sample = el("div", {
      class: "preset-sample",
      style: {
        fontFamily: cssFontFamily(s.font_family),
        fontWeight: s.bold ? "700" : "400",
        textTransform: s.uppercase ? "uppercase" : "none",
        background: s.background === "box" ? hexToRgba(s.box_color, s.box_opacity) : "transparent",
        padding: s.background === "box" ? "1px 6px" : "0",
        webkitTextStroke: s.background === "outline" ? `${Math.min(3, s.outline_width * 0.6)}px ${s.outline_color}` : "",
        paintOrder: "stroke fill",
        color: hexToRgba(s.text_color, s.text_opacity),
      },
    }, el("span", { style: { color: s.highlight_color } }, "Hello "), "world");
    const del = !p.builtin
      ? el("span", { class: "del", title: "Delete preset", onclick: async (e) => {
          e.stopPropagation();
          await api.deletePreset(p.id);
          load();
        } }, "×")
      : null;
    return el("div", { class: "preset", title: p.description || p.name, onclick: () => apply(p.style) }, sample, el("div", { class: "preset-name" }, p.name), del);
  };

  const load = async () => {
    presets = await api.presets();
    grid.replaceChildren(...presets.map(card));
  };

  importInput.addEventListener("change", async () => {
    const file = importInput.files[0];
    importInput.value = "";
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      apply(data.style || data);
      toast("Preset applied", "ok");
    } catch {
      toast("That file isn't a valid preset", "error");
    }
  });

  const saveCurrent = async () => {
    const name = await askText("Preset name");
    if (!name) return;
    await api.savePreset(name, state.style);
    await load();
    toast(`Saved preset “${name}”`, "ok");
  };

  const exportJson = () => {
    const style = Object.fromEntries(Object.entries(state.style).filter(([k]) => !CANVAS_KEYS.includes(k)));
    downloadBlob("caption-style.json", JSON.stringify({ name: "My style", style }, null, 2));
  };

  load();
  state.on("fonts", () => grid.replaceChildren(...presets.map(card)));
  return {
    node: el("div", { class: "ctl" },
      grid,
      el("div", { class: "preset-actions" },
        el("button", { class: "btn small", type: "button", onclick: saveCurrent }, "Save current"),
        el("button", { class: "btn small", type: "button", onclick: exportJson }, "Export"),
        el("button", { class: "btn small", type: "button", onclick: () => importInput.click() }, "Import"),
      ),
      importInput,
    ),
    update: () => {},
  };
}

export function buildStylePanel() {
  const root = $("#stylePanel");
  const isBox = (s) => s.background === "box";
  const isOutline = (s) => s.background === "outline";

  const sections = [
    ["Presets", [presetControls()]],
    ["Text", [
      fontPicker(),
      range("font_size", "Size", { min: 16, max: 200, unit: "px" }),
      toggles([["bold", "B", { fontWeight: "700" }], ["italic", "I", { fontStyle: "italic" }], ["uppercase", "AA", { fontSize: "12px", letterSpacing: ".05em" }]]),
      range("letter_spacing", "Letter spacing", { min: -5, max: 30, step: 0.5, unit: "px" }),
    ]],
    ["Colors", [
      color("text_color", "Primary · text", "text_opacity"),
      color("highlight_color", "Secondary · active word"),
      segmented("highlight_mode", "Highlight style", [
        ["word", "Active word", "Only the word being spoken is highlighted"],
        ["progressive", "Progressive", "Spoken words stay highlighted"],
        ["fill", "Smooth fill", "Each word fills left to right (classic karaoke)"],
      ]),
      visible(range("active_scale", "Active word size", { min: 100, max: 160, unit: "%" }), (s) => s.highlight_mode !== "fill"),
    ]],
    ["Background", [
      segmented("background", null, [["box", "Box"], ["outline", "Outline"], ["none", "None"]]),
      visible(color("box_color", "Overlay color", "box_opacity"), isBox),
      visible(pair(range("padding_x", "Padding X", { min: 0, max: 80, unit: "px" }), range("padding_y", "Padding Y", { min: 0, max: 60, unit: "px" })), isBox),
      visible(color("outline_color", "Outline color"), isOutline),
      visible(range("outline_width", "Outline width", { min: 0, max: 16, step: 0.5, unit: "px" }), isOutline),
      color("shadow_color", "Shadow color", "shadow_opacity"),
      range("shadow_depth", "Shadow distance", { min: 0, max: 20, step: 0.5, unit: "px" }),
    ]],
    ["Position & lines", [
      segmented("position", "Position", [["top", "Top"], ["center", "Middle"], ["bottom", "Bottom"]]),
      visible(range("margin_v", "Distance from edge", { min: 0, max: 800, unit: "px" }), (s) => s.position !== "center"),
      range("margin_h", "Side margin", { min: 0, max: 600, unit: "px" }),
      range("max_words", "Max words per line", { min: 1, max: 15 }),
      range("max_chars", "Max characters per line", { min: 0, max: 80, hint: "0 = no limit" }),
      range("pause_break", "New line after a pause of", { min: 0, max: 3, step: 0.05, unit: "s", hint: "0 = off" }),
      range("line_hold", "Keep line on screen after speech", { min: 0, max: 2, step: 0.05, unit: "s" }),
      toggles([["sentence_break", "New line after . ? !"]]),
    ]],
    ["Canvas", [canvasControls()]],
  ];

  const controls = [];
  root.replaceChildren(...sections.map(([title, items], idx) => {
    controls.push(...items);
    const section = el("section", { class: `sp-section ${idx === 4 ? "collapsed" : ""}` });
    const head = el("button", { class: "sp-head", type: "button", onclick: () => section.classList.toggle("collapsed") },
      title, el("span", { class: "chev", html: '<svg viewBox="0 0 24 24" class="icon"><path d="m6 9 6 6 6-6"/></svg>' }));
    section.append(head, el("div", { class: "sp-body" }, items.map((c) => c.node)));
    return section;
  }));

  const sync = () => state.style.font_family && controls.forEach((c) => c.update());
  state.on("style", sync);
  state.on("fonts", sync);
  sync();
}
