import { api } from "./api.js";
import { Editor } from "./editor.js";
import { ExportDialog } from "./export.js";
import { loadFontList } from "./fonts.js";
import { Preview } from "./preview.js";
import { state } from "./state.js";
import { clearSteps, renderSteps } from "./steps.js";
import { buildStylePanel } from "./style-panel.js";
import { $, debounce, el, fmtBytes, fmtTime, toast } from "./util.js";

const VIEWS = ["viewHome", "viewWorking", "viewError", "viewStudio"];
const SOLID_BACKGROUNDS = [
  ["dark", "Dark", "#0B0F17"],
  ["light", "Light", "#F1F5F9"],
  ["green", "Green screen", "#00B140"],
  ["checker", "Transparent", null],
];

function show(view) {
  VIEWS.forEach((v) => ($(`#${v}`).hidden = v !== view));
  const inStudio = view === "viewStudio";
  $("#btnExport").hidden = !inStudio;
  $("#saveState").hidden = !inStudio;
  $("#topbarProject").hidden = !(inStudio || view === "viewWorking" || view === "viewError");
}

// ---------------------------------------------------------------- player

class Player {
  constructor() {
    this.audio = $("#player");
    this.video = $("#bgVideo");
    this.stopAt = null;
    this.syncVideo = false;
    this.audio.addEventListener("play", () => { if (this.video.src) this.video.play().catch(() => {}); state.emit("playstate", true); });
    this.audio.addEventListener("pause", () => { this.video.pause(); state.emit("playstate", false); });
    this.audio.addEventListener("seeked", () => state.emit("seeked"));
    this.audio.addEventListener("loadedmetadata", () => state.emit("duration"));
  }
  get time() { return this.audio.currentTime || 0; }
  get duration() { return this.audio.duration || state.project?.media?.duration || 0; }
  get playing() { return !this.audio.paused; }
  load(url) {
    this.audio.src = url;
    this.audio.load();
  }
  seek(t) {
    this.audio.currentTime = Math.max(0, Math.min(t, this.duration || t));
    if (this.syncVideo) this.video.currentTime = this.audio.currentTime;
    this.stopAt = null;
  }
  toggle() { this.playing ? this.audio.pause() : this.play(); }
  play() { this.audio.play().catch((e) => toast(`Can't play: ${e.message}`, "error")); }
  pause() { this.audio.pause(); }
  playRange(start, end) {
    this.seek(start);
    this.stopAt = end;
    this.play();
  }
  setRate(r) { this.audio.playbackRate = r; this.video.playbackRate = r; }
  tick() {
    if (this.stopAt !== null && this.time >= this.stopAt) {
      this.pause();
      this.stopAt = null;
    }
    if (this.syncVideo && this.video.readyState >= 2 && Math.abs(this.video.currentTime - this.time) > 0.25) {
      this.video.currentTime = this.time;
    }
  }
}

// ---------------------------------------------------------------- studio

let player, preview, editor, exportDialog;
let studioReady = false;
let rafId = null;
let exactSeq = 0;

const saveState = $("#saveState");
let pendingSave = false;

const save = debounce(async () => {
  if (!state.project) return;
  saveState.textContent = "Saving…";
  saveState.className = "save-state saving";
  try {
    await api.updateProject(state.project.id, { words: state.words, style: state.style, preview: state.preview });
    pendingSave = false;
    saveState.textContent = "All changes saved";
    saveState.className = "save-state";
  } catch (err) {
    saveState.textContent = "Couldn't save";
    toast(`Saving failed: ${err.message}`, "error");
  }
}, 800);

async function saveNow() {
  if (pendingSave) await save.flush();
}

function initStudio() {
  if (studioReady) return;
  studioReady = true;
  player = new Player();
  preview = new Preview();
  editor = new Editor(player);
  exportDialog = new ExportDialog(saveNow);
  buildStylePanel();

  state.on("dirty", () => {
    pendingSave = true;
    saveState.textContent = "Unsaved changes";
    saveState.className = "save-state saving";
    save();
  });
  state.on("style", ({ keys }) => {
    preview.applyStyle();
    if (keys.some((k) => k === "width" || k === "height")) preview.fit();
    requestExact();
  });
  state.on("fonts", () => preview.applyStyle());
  state.on("words", () => { preview.invalidate(); drawScrubLines(); requestExact(); });
  state.on("preview", () => applyBackground());
  state.on("playstate", (playing) => {
    $("#iconPlay").hidden = playing;
    $("#iconPause").hidden = !playing;
    if (playing) preview.hideExact();
    else requestExact();
  });
  state.on("seeked", () => requestExact());
  state.on("duration", () => drawScrubLines());

  $("#btnPlay").addEventListener("click", () => player.toggle());
  $("#speedSelect").addEventListener("change", (e) => player.setRate(Number(e.target.value)));
  $("#btnExport").addEventListener("click", () => exportDialog.open());
  $("#exactToggle").addEventListener("change", (e) => {
    if (e.target.checked) requestExact.flush();
    else preview.hideExact();
  });
  $("#templateInput").addEventListener("change", uploadTemplate);
  setupScrubber();
  new ResizeObserver(() => drawScrubLines()).observe($("#scrubber"));

  window.addEventListener("beforeunload", (e) => {
    if (pendingSave) { save.flush(); e.preventDefault(); e.returnValue = ""; }
  });
}

function loop() {
  const t = player.time;
  player.tick();
  preview.render(t);
  editor.tick(t, player.playing);
  const dur = player.duration || 1;
  $("#timeLabel").textContent = fmtTime(t);
  const pct = `${Math.min(100, (t / dur) * 100)}%`;
  $("#scrubFill").style.width = pct;
  $("#scrubHead").style.left = pct;
  rafId = requestAnimationFrame(loop);
}

function setupScrubber() {
  const scrubber = $("#scrubber");
  const seekFromEvent = (e) => {
    const r = scrubber.getBoundingClientRect();
    player.seek(((e.clientX - r.left) / r.width) * (player.duration || 0));
  };
  scrubber.addEventListener("pointerdown", (e) => {
    scrubber.setPointerCapture(e.pointerId);
    seekFromEvent(e);
    const move = (ev) => seekFromEvent(ev);
    scrubber.addEventListener("pointermove", move);
    scrubber.addEventListener("pointerup", () => scrubber.removeEventListener("pointermove", move), { once: true });
  });
}

function drawScrubLines() {
  const canvas = $("#scrubLines");
  const dpr = window.devicePixelRatio || 1;
  const { width, height } = canvas.getBoundingClientRect();
  if (!width) return;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const dur = player?.duration || state.project?.media?.duration || 1;
  ctx.fillStyle = "rgba(168, 85, 247, 0.35)";
  for (const line of state.lines) {
    const x = (line.start / dur) * width;
    const w = Math.max(1, ((line.end - line.start) / dur) * width - 1);
    ctx.fillRect(x, height * 0.3, w, height * 0.4);
  }
  $("#durationLabel").textContent = fmtTime(dur, false);
}

const requestExact = debounce(async () => {
  if (!$("#exactToggle").checked || !state.project || player.playing) return;
  const seq = ++exactSeq;
  preview.setBadge("Exact render (libass)", "exact loading");
  try {
    const blob = await api.snapshot(state.project.id, { t: player.time, style: state.style, words: state.words });
    if (seq !== exactSeq || player.playing) return;
    preview.showExact(URL.createObjectURL(blob));
  } catch (err) {
    if (seq === exactSeq) {
      preview.hideExact();
      toast(`Exact render failed: ${err.message}`, "error");
    }
  }
}, 250);

// ---------------------------------------------------------------- backgrounds

function backgroundChips() {
  const p = state.preview;
  const chips = [];
  const chip = (id, label, swatch, onSelect, onDelete) => {
    const active = id === (p.background === "template" ? `template:${p.template_id}` : p.background);
    const node = el("button", { type: "button", class: `bg-chip ${active ? "active" : ""}`, role: "radio", "aria-checked": String(active), onclick: onSelect },
      el("span", { class: "sw", style: swatch }), label,
      onDelete ? el("span", { class: "x", title: "Remove template", onclick: (e) => { e.stopPropagation(); onDelete(); } }, "×") : null);
    chips.push(node);
  };

  for (const t of state.templates) {
    chip(`template:${t.id}`, t.name,
      t.builtin ? { background: "linear-gradient(135deg, #6d28d9, #0891b2)" } : { background: "linear-gradient(135deg, #334155, #64748b)" },
      () => state.setPreview({ background: "template", template_id: t.id }),
      t.builtin ? null : async () => {
        await api.deleteTemplate(t.id);
        state.templates = await api.templates();
        if (state.preview.template_id === t.id) state.setPreview({ background: "dark", template_id: null });
        else renderBgPicker();
      });
  }
  if (state.project.media?.has_video) {
    chip("media", "My video", { background: "linear-gradient(135deg, #f59e0b, #ef4444)" }, () => state.setPreview({ background: "media" }));
  }
  for (const [id, label, color] of SOLID_BACKGROUNDS) {
    chip(id, label, color ? { background: color } : { background: "repeating-conic-gradient(#555 0 25%, #999 0 50%) 50% / 10px 10px" },
      () => state.setPreview({ background: id }));
  }
  return chips;
}

function renderBgPicker() {
  $("#bgPicker").replaceChildren(...backgroundChips());
}

function applyBackground() {
  const p = state.preview;
  const video = $("#bgVideo");
  const bg = $("#stageBg");
  const media = state.project.media || {};
  let src = null;
  player.syncVideo = false;

  if (p.background === "template" && state.templates.some((t) => t.id === p.template_id)) {
    src = api.templateUrl(p.template_id);
    video.loop = true;
  } else if (p.background === "media" && media.has_video) {
    src = api.mediaUrl(state.project.id);
    video.loop = false;
    player.syncVideo = true;
  }

  if (src) {
    if (video.getAttribute("src") !== src) {
      video.src = src;
      video.load();
    }
    video.hidden = false;
    video.onloadeddata = () => {
      if (player.syncVideo) video.currentTime = player.time;
      if (player.playing) video.play().catch(() => {});
    };
    if (player.playing) video.play().catch(() => {});
    bg.className = "stage-bg";
    bg.style.background = "#000";
  } else {
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.hidden = true;
    const solid = SOLID_BACKGROUNDS.find(([id]) => id === p.background) || SOLID_BACKGROUNDS[0];
    bg.className = `stage-bg ${solid[0] === "checker" ? "bg-checker" : ""}`;
    bg.style.background = solid[2] || "";
  }
  renderBgPicker();
}

async function uploadTemplate(e) {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  toast(`Uploading “${file.name}”…`);
  try {
    const t = await api.uploadTemplate(file);
    state.templates = await api.templates();
    state.setPreview({ background: "template", template_id: t.id });
    toast(`Template “${t.name}” added`, "ok");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function openStudio(project) {
  initStudio();
  state.templates = await api.templates().catch(() => []);
  if (location.hash !== `#/p/${project.id}`) return;
  show("viewStudio");
  state.load(project);
  $("#projectName").value = project.name;
  saveState.textContent = "All changes saved";
  saveState.className = "save-state";
  player.load(api.mediaUrl(project.id));
  applyBackground();
  preview.applyStyle();
  drawScrubLines();
  if (!rafId) loop();
  if (!state.templates.length) {
    setTimeout(async () => {
      state.templates = await api.templates().catch(() => []);
      if (state.templates.length && state.project?.id === project.id) applyBackground();
    }, 4000);
  }
}

function closeStudio() {
  if (rafId) cancelAnimationFrame(rafId);
  rafId = null;
  if (player) {
    player.pause();
    player.audio.removeAttribute("src");
    player.audio.load();
  }
  if (pendingSave) save.flush();
}

// ---------------------------------------------------------------- working / error

let workPoll = null;

async function watchProject(id) {
  clearTimeout(workPoll);
  let project;
  try {
    project = await api.project(id);
  } catch (err) {
    toast(err.message, "error");
    location.hash = "#/";
    return;
  }
  if (location.hash !== `#/p/${id}`) return;
  $("#projectName").value = project.name;

  if (project.status === "ready") {
    openStudio(project);
    return;
  }
  if (project.status === "error") {
    show("viewError");
    $("#errorMessage").textContent = project.error || "Unknown error";
    $("#btnRetry").onclick = async () => {
      try {
        await api.retranscribe(id, { ...(project.transcribe || {}), replace: true });
        watchProject(id);
      } catch (err) {
        toast(err.message, "error");
      }
    };
    return;
  }

  show("viewWorking");
  const job = project.job;
  const steps = job?.steps || [];
  $("#workTitle").textContent = project.status === "transcribing" ? "Transcribing" : "Preparing your file";
  $("#workMessage").textContent = job?.message || "Starting…";
  $("#workMessage").hidden = steps.length > 0;
  renderSteps($("#workSteps"), steps);
  $("#workPct").textContent = job ? `${Math.round(job.progress * 100)}% overall` : "";
  $("#workElapsed").textContent = job ? `${fmtTime(job.elapsed, false)} elapsed` : "";
  $("#btnCancelWork").onclick = () => job && api.cancelJob(job.id);
  workPoll = setTimeout(() => watchProject(id), 1000);
}

// ---------------------------------------------------------------- home

const onHome = () => !/^#\/p\//.test(location.hash);

async function renderHome() {
  $("#uploadProgress").hidden = true;
  clearSteps($("#uploadSteps"));
  const [health, projects] = await Promise.all([api.health().catch(() => null), api.projects().catch(() => [])]);
  if (!onHome()) return;
  show("viewHome");
  const warnings = $("#healthWarnings");
  warnings.replaceChildren();
  if (health && !health.ffmpeg) {
    warnings.append(el("div", { class: "notice warn", html: "<b>ffmpeg wasn't found.</b> Install it and make sure <code>ffmpeg</code> and <code>ffprobe</code> are on your PATH, then restart the app." }));
  }
  if (health && !health.whisper) {
    warnings.append(el("div", { class: "notice warn", html: "<b>faster-whisper isn't installed</b>, so files can't be transcribed. Run <code>pip install faster-whisper</code>, or import a words.json." }));
  }

  $("#recentSection").hidden = !projects.length;
  $("#recentList").replaceChildren(...projects.map((p) => el("a", { class: "recent-item", href: `#/p/${p.id}` },
    el("span", { class: "ri-icon", html: '<svg viewBox="0 0 24 24" class="icon"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>' }),
    el("span", { class: "ri-body" },
      el("div", { class: "ri-name" }, p.name || "Untitled"),
      el("div", { class: "ri-meta" }, [
        p.status === "ready" ? `${p.words.toLocaleString()} words` : p.status,
        p.duration ? fmtTime(p.duration, false) : null,
        p.updated ? new Date(p.updated * 1000).toLocaleDateString() : null,
      ].filter(Boolean).join(" · "))),
    el("button", { class: "btn small ghost icon-only danger", title: "Delete project", onclick: async (e) => {
      e.preventDefault();
      if (!confirm(`Delete “${p.name}”? This removes its media and exports.`)) return;
      await api.deleteProject(p.id);
      renderHome();
    }, html: '<svg viewBox="0 0 24 24" class="icon"><path d="M4 7h16M10 11v6M14 11v6M5 7l1 13h12l1-13M9 7V4h6v3"/></svg>' }),
  )));
}

function setupUpload() {
  const zone = $("#dropzone");
  const input = $("#fileInput");
  input.addEventListener("change", () => input.files[0] && startUpload(input.files[0]));
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) startUpload(file);
  });
}

async function startUpload(file) {
  const fd = new FormData();
  fd.append("file", file);
  const words = $("#wordsInput").files[0];
  if (words) fd.append("words_file", words);
  fd.append("model", $("#optModel").value);
  fd.append("language", $("#optLanguage").value);
  fd.append("device", $("#optDevice").value);
  fd.append("prompt", $("#optPrompt").value);
  localStorage.setItem("kc-upload-options", JSON.stringify({
    model: $("#optModel").value, language: $("#optLanguage").value, device: $("#optDevice").value, prompt: $("#optPrompt").value,
  }));

  const upcoming = [["probe", "Reading media"], ["preview", "Preparing preview"],
    ...(words ? [["import", "Importing word timings"]]
      : [["decode", "Decoding audio"], ["download", "Downloading model"], ["load", "Loading model"], ["transcribe", "Transcribing"]])];
  const started = performance.now();
  const drawUpload = (p) => renderSteps($("#uploadSteps"), [
    {
      id: "upload", label: `Uploading ${file.name}`, status: "active", progress: p,
      elapsed: (performance.now() - started) / 1000,
      detail: p < 1 ? `${fmtBytes(Math.round(p * file.size))} / ${fmtBytes(file.size)}` : "Saving on the server…",
    },
    ...upcoming.map(([id, label]) => ({ id, label, status: "pending", progress: 0 })),
  ]);
  $("#uploadProgress").hidden = false;
  drawUpload(0);
  try {
    const project = await api.createProject(fd, drawUpload);
    $("#fileInput").value = "";
    $("#wordsInput").value = "";
    location.hash = `#/p/${project.id}`;
  } catch (err) {
    $("#uploadProgress").hidden = true;
    toast(err.message, "error");
  }
}

function restoreUploadOptions() {
  try {
    const saved = JSON.parse(localStorage.getItem("kc-upload-options") || "{}");
    for (const [key, id] of [["model", "optModel"], ["language", "optLanguage"], ["device", "optDevice"], ["prompt", "optPrompt"]]) {
      if (saved[key] !== undefined) $(`#${id}`).value = saved[key];
    }
  } catch {}
}

// ---------------------------------------------------------------- routing

async function route() {
  clearTimeout(workPoll);
  const match = /^#\/p\/([a-f0-9]{12})$/.exec(location.hash);
  if (match) {
    if (state.project?.id !== match[1]) closeStudio();
    watchProject(match[1]);
  } else {
    closeStudio();
    state.project = null;
    renderHome();
  }
}

$("#projectName").addEventListener("change", async (e) => {
  const name = e.target.value.trim();
  const id = location.hash.split("/")[2];
  if (!name || !id) return;
  try {
    await api.updateProject(id, { name });
    if (state.project) state.project.name = name;
  } catch (err) {
    toast(err.message, "error");
  }
});
$("#projectName").addEventListener("keydown", (e) => e.key === "Enter" && e.target.blur());

async function boot() {
  setupUpload();
  restoreUploadOptions();
  loadFontList();
  state.defaults = await api.styleDefaults();
  window.addEventListener("hashchange", route);
  route();
}

boot();
