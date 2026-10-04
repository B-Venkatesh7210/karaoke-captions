import { api } from "./api.js";
import { state } from "./state.js";

const loaded = new Map();
const DEFAULT_SCALE = 0.82;

function alias(name) {
  return `kc-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
}

/**
 * Serve the exact font files libass will use to the browser, so the live
 * preview and the export use the same glyphs.
 */
export function ensureFont(name) {
  const family = state.family(name);
  if (!family) return Promise.resolve(false);
  if (loaded.has(family.name)) return loaded.get(family.name);

  const faces = family.faces
    .filter((f) => !f.collection)
    .map((f) => {
      const face = new FontFace(alias(family.name), `url(${api.fontFaceUrl(f.id)})`, {
        weight: String(f.weight || 400),
        style: f.italic ? "italic" : "normal",
      });
      document.fonts.add(face);
      return face.load().catch(() => null);
    });
  const promise = Promise.all(faces).then(() => true);
  loaded.set(family.name, promise);
  return promise;
}

export function cssFontFamily(name) {
  const family = state.family(name);
  const quoted = `"${String(name).replace(/"/g, "")}"`;
  if (family && family.faces.some((f) => !f.collection)) return `"${alias(family.name)}", ${quoted}, sans-serif`;
  return `${quoted}, sans-serif`;
}

/** libass sizes text so that the font's ascent + descent equals the ASS font size. */
export function fontScale(name) {
  return state.family(name)?.scale || DEFAULT_SCALE;
}

export async function loadFontList() {
  for (let attempt = 0; attempt < 60; attempt++) {
    const data = await api.fonts();
    if (data.ready) {
      state.fonts = data;
      state.emit("fonts");
      return;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
}
