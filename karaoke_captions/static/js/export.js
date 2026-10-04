import { api } from "./api.js";
import { state } from "./state.js";
import { $, el, fmtBytes, fmtTime, toast } from "./util.js";

const FORMATS = [
  {
    id: "mov",
    title: "Transparent overlay",
    ext: ".mov · ProRes 4444",
    text: "Drop it on top of your video in Premiere Pro, DaVinci Resolve or Final Cut. Big files, best quality.",
  },
  {
    id: "webm",
    title: "Transparent overlay",
    ext: ".webm · VP9 alpha",
    text: "Much smaller files with transparency. Works in browsers, OBS, After Effects and many editors.",
  },
  {
    id: "mp4",
    title: "Finished video",
    ext: ".mp4 · H.264",
    text: "Captions burned onto your video, a template or a solid color, with your audio. Ready to post.",
  },
];

const SOLIDS = { dark: "#0B0F17", light: "#F1F5F9", green: "#00B140", checker: "#000000" };

export class ExportDialog {
  constructor(saveNow) {
    this.dialog = $("#exportDialog");
    this.body = $("#exportBody");
    this.saveNow = saveNow;
    this.options = null;
    this.job = null;
    this.poll = null;
    this.dialog.addEventListener("close", () => clearTimeout(this.poll));
  }

  defaultOptions() {
    const p = state.preview;
    const media = state.project.media || {};
    let background = "color";
    if (p.background === "media" && media.has_video) background = "media";
    else if (p.background === "template" && p.template_id) background = "template";
    return {
      format: "mov",
      background,
      template_id: p.template_id || state.templates[0]?.id || null,
      bg_color: SOLIDS[p.background] || "#000000",
      include_audio: false,
      fit: "cover",
    };
  }

  async open() {
    await this.saveNow();
    this.options ||= this.defaultOptions();
    this.render();
    this.dialog.showModal();
    this.loadExports();
    if (this.job && ["queued", "running"].includes(this.job.status)) this.watch();
  }

  set(patch) {
    Object.assign(this.options, patch);
    this.render();
  }

  render() {
    const o = this.options;
    const media = state.project.media || {};
    const s = state.style;

    const formats = el("div", { class: "formats" },
      FORMATS.map((f) => el("button", { type: "button", class: `format ${o.format === f.id ? "on" : ""}`, onclick: () => this.set({ format: f.id }) },
        el("strong", {}, f.title), el("span", { class: "ext" }, f.ext), el("p", {}, f.text))));

    const opts = el("div", { class: "export-opts" });
    if (o.format === "mp4") {
      const bgSelect = el("select", {},
        media.has_video ? el("option", { value: "media" }, "My uploaded video") : null,
        state.templates.length ? el("option", { value: "template" }, "A template video") : null,
        el("option", { value: "color" }, "Solid color"));
      bgSelect.value = o.background === "media" && !media.has_video ? "color" : o.background;
      bgSelect.addEventListener("change", () => this.set({ background: bgSelect.value }));
      opts.append(el("label", { class: "field" }, el("span", {}, "Background"), bgSelect));

      if (bgSelect.value === "template") {
        const tpl = el("select", {}, state.templates.map((t) => el("option", { value: t.id }, t.name)));
        tpl.value = o.template_id || state.templates[0]?.id;
        tpl.addEventListener("change", () => this.set({ template_id: tpl.value }));
        opts.append(el("label", { class: "field" }, el("span", {}, "Template (loops for the whole duration)"), tpl));
      }
      if (bgSelect.value === "color") {
        const color = el("input", { type: "color", value: o.bg_color.toLowerCase() });
        color.addEventListener("input", () => (o.bg_color = color.value.toUpperCase()));
        opts.append(el("label", { class: "field" }, el("span", {}, "Color"), el("div", { class: "color-ctl" }, el("div", { class: "color-swatch" }, color))));
      }
      if (bgSelect.value !== "color") {
        const fit = el("select", {}, el("option", { value: "cover" }, "Fill the frame (crop edges)"), el("option", { value: "contain" }, "Fit inside (add bars)"));
        fit.value = o.fit;
        fit.addEventListener("change", () => (o.fit = fit.value));
        opts.append(el("label", { class: "field" }, el("span", {}, "Framing"), fit));
      }
    } else {
      const audio = el("input", { type: "checkbox", checked: o.include_audio });
      audio.addEventListener("change", () => (o.include_audio = audio.checked));
      opts.append(el("label", { class: "switch" }, audio, el("span", { class: "switch-track" }), el("span", {}, "Include the audio track")));
    }

    const summary = el("div", { class: "export-summary" },
      el("span", {}, "Size ", el("b", {}, `${s.width}×${s.height}`)),
      el("span", {}, "Frame rate ", el("b", {}, `${s.fps} fps`)),
      el("span", {}, "Length ", el("b", {}, fmtTime(media.duration || 0, false))),
      el("span", {}, "Font ", el("b", {}, s.font_family)),
    );

    const renderBox = el("div", { class: "render-box", id: "renderBox" });
    this.renderBox = renderBox;
    this.paintJob();

    const quick = el("div", { class: "quick-downloads" },
      el("a", { class: "btn small", href: `/api/projects/${state.project.id}/captions.ass`, download: true }, "Subtitles (.ass)"),
      el("a", { class: "btn small", href: `/api/projects/${state.project.id}/words.json`, download: true }, "Transcript (words.json)"),
    );

    this.exportsList = el("div", { class: "exports-list" });

    this.body.replaceChildren(
      el("div", { class: "section-label" }, "Format"), formats,
      opts, summary, renderBox,
      el("div", { class: "section-label" }, "Other downloads"), quick,
      el("div", { class: "section-label" }, "Previous exports"), this.exportsList,
    );
    this.paintExports();
  }

  paintJob() {
    const box = this.renderBox;
    if (!box) return;
    const job = this.job;
    const running = job && ["queued", "running"].includes(job.status);

    if (running) {
      const bar = el("div", { style: { width: `${(job.progress * 100).toFixed(1)}%` } });
      box.replaceChildren(
        el("div", { class: "row" }, el("span", {}, job.message || "Rendering…"), el("span", {}, `${Math.round(job.progress * 100)}% · ${fmtTime(job.elapsed, false)}`)),
        el("div", { class: `progress big ${job.progress <= 0 ? "indeterminate" : ""}` }, bar),
        el("div", { class: "row-gap end" }, el("button", { type: "button", class: "btn small ghost", onclick: () => api.cancelJob(job.id) }, "Cancel")),
      );
      return;
    }

    const start = el("button", { type: "button", class: "btn primary", onclick: () => this.start() }, "Render video");
    if (job?.status === "done") {
      box.replaceChildren(
        el("div", { class: "render-done" },
          el("span", { class: "ok" }, `✓ Done in ${fmtTime(job.elapsed, false)}`),
          el("a", { class: "btn primary", href: `/api/projects/${state.project.id}/renders/${job.id}`, download: true }, "Download"),
          el("button", { type: "button", class: "btn", onclick: () => this.start() }, "Render again")),
      );
    } else if (job?.status === "error") {
      box.replaceChildren(el("p", { class: "error-text" }, job.error || "Render failed"), el("div", { class: "row-gap end" }, start));
    } else {
      box.replaceChildren(el("div", { class: "row-gap end" }, job?.status === "cancelled" ? el("span", { class: "muted small" }, "Cancelled") : null, start));
    }
  }

  async start() {
    try {
      await this.saveNow();
      this.job = await api.render(state.project.id, { style: state.style, words: state.words, output: this.options });
      this.paintJob();
      this.watch();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  watch() {
    clearTimeout(this.poll);
    const tick = async () => {
      try {
        this.job = await api.job(this.job.id);
      } catch {
        return;
      }
      this.paintJob();
      if (["queued", "running"].includes(this.job.status)) this.poll = setTimeout(tick, 700);
      else if (this.job.status === "done") { this.loadExports(); toast("Export finished", "ok"); }
    };
    this.poll = setTimeout(tick, 400);
  }

  async loadExports() {
    try {
      this.exports = await api.renders(state.project.id);
    } catch {
      this.exports = [];
    }
    this.paintExports();
  }

  paintExports() {
    if (!this.exportsList) return;
    const items = this.exports || [];
    if (!items.length) {
      this.exportsList.replaceChildren(el("span", { class: "muted small" }, "Nothing exported yet."));
      return;
    }
    this.exportsList.replaceChildren(...items.map((r) => el("div", { class: "export-row" },
      el("span", { class: "name", title: r.file }, r.file),
      el("span", { class: "meta" }, `${fmtBytes(r.size)} · ${new Date(r.created * 1000).toLocaleString()}`),
      el("a", { class: "btn small", href: r.url, download: true }, "Download"),
      el("button", { type: "button", class: "btn small ghost danger", title: "Delete", onclick: async () => {
        await api.deleteRender(state.project.id, r.id);
        this.loadExports();
      } }, "×"),
    )));
  }
}
