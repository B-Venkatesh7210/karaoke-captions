// Mirrors karaoke_captions/core/layout.py so the preview groups words exactly
// like the exported subtitles. Keep the two in sync.

const SENTENCE_END = [".", "?", "!", "…"];
const TRAILING_QUOTES = /["'”’)\]]+$/;

export function endsSentence(text) {
  const t = text.replace(TRAILING_QUOTES, "");
  return SENTENCE_END.some((p) => t.endsWith(p));
}

/** Returns [{start, end, from, to}] where from/to are word indexes (inclusive). */
export function buildLines(words, style) {
  const groups = [];
  let current = [];
  const chars = (idxs) => idxs.reduce((n, i, k) => n + words[i].text.length + (k ? 1 : 0), 0);

  for (let i = 0; i < words.length; i++) {
    const word = words[i];
    if (!word.text.trim()) continue;
    if (current.length && style.max_chars > 0 && chars(current) + 1 + word.text.length > style.max_chars) {
      groups.push(current);
      current = [];
    }
    current.push(i);
    const next = words[i + 1];
    const brk =
      current.length >= style.max_words ||
      word.break_after ||
      (style.sentence_break && endsSentence(word.text)) ||
      (next && style.pause_break > 0 && next.start - word.end >= style.pause_break);
    if (brk) {
      groups.push(current);
      current = [];
    }
  }
  if (current.length) groups.push(current);

  return groups.map((g, idx) => {
    const last = words[g[g.length - 1]];
    let end = last.end;
    if (style.line_hold > 0) {
      end += style.line_hold;
      const next = groups[idx + 1];
      if (next) end = Math.min(end, words[next[0]].start);
    }
    return { start: words[g[0]].start, end: Math.max(end, last.end), from: g[0], to: g[g.length - 1] };
  });
}

/** Index of the line visible at time t, or -1. */
export function lineAt(lines, t) {
  let lo = 0, hi = lines.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  if (found >= 0 && t < lines[found].end) return found;
  return -1;
}

/** Index of the line whose time span is closest to t (for editor following). */
export function nearestLine(lines, t) {
  let lo = 0, hi = lines.length - 1, found = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return lines.length ? found : -1;
}

/** Active word index within a visible line: the last word that has started. */
export function activeWord(words, line, t) {
  let active = line.from;
  for (let i = line.from; i <= line.to; i++) {
    if (words[i].start <= t) active = i;
    else break;
  }
  return active;
}
