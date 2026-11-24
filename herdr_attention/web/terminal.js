// v0.9.8 live terminal view: an agent's screen as Herdr shows it, coloured, read-only.
// The pure helpers (ansiLines, scrolledBy) are tested in tests/js/terminal.test.js.
// The view never sends keys: it only GETs /api/terminal. The reply button opens the normal composer.
"use strict";

// Tokyo Night (the Omarchy theme) for the 16 basic colours; 256-colour and truecolour codes are
// drawn as the agent asked (the CLI colours), the default colours follow the page theme.
const TERM_PALETTE = ["#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
  "#414868", "#ff899d", "#9fe044", "#faba4a", "#8db0ff", "#c7a9ff", "#a4daff", "#c0caf5"];

function term256(n) {
  if (n < 16) return TERM_PALETTE[n];
  if (n < 232) {
    const v = [0, 95, 135, 175, 215, 255]; n -= 16;
    return `rgb(${v[Math.floor(n / 36)]},${v[Math.floor(n / 6) % 6]},${v[n % 6]})`;
  }
  const g = 8 + (n - 232) * 10;
  return `rgb(${g},${g},${g})`;
}

function termEscape(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const TERM_RESET = () => ({ fg: null, bg: null, bold: false, dim: false, italic: false, under: false, inverse: false, hidden: false, strike: false });

// Apply one SGR parameter list ("1;38;2;10;20;30") to a style. Colon forms (38:2::r:g:b) work too.
function termSgr(style, params) {
  const p = params === "" ? [0] : params.split(/[;:]/).map((x) => (x === "" ? NaN : Number(x)));
  const clamp = (x) => Math.max(0, Math.min(255, x | 0));
  for (let i = 0; i < p.length; i++) {
    const n = p[i];
    if (n === 0 || Number.isNaN(n) && i === 0 && p.length === 1) Object.assign(style, TERM_RESET());
    else if (n === 1) style.bold = true;
    else if (n === 2) style.dim = true;
    else if (n === 3) style.italic = true;
    else if (n === 4) style.under = true;
    else if (n === 7) style.inverse = true;
    else if (n === 8) style.hidden = true;
    else if (n === 9) style.strike = true;
    else if (n === 21 || n === 22) { style.bold = false; style.dim = false; }
    else if (n === 23) style.italic = false;
    else if (n === 24) style.under = false;
    else if (n === 27) style.inverse = false;
    else if (n === 28) style.hidden = false;
    else if (n === 29) style.strike = false;
    else if (n >= 30 && n <= 37) style.fg = TERM_PALETTE[n - 30];
    else if (n >= 90 && n <= 97) style.fg = TERM_PALETTE[n - 90 + 8];
    else if (n >= 40 && n <= 47) style.bg = TERM_PALETTE[n - 40];
    else if (n >= 100 && n <= 107) style.bg = TERM_PALETTE[n - 100 + 8];
    else if (n === 39) style.fg = null;
    else if (n === 49) style.bg = null;
    else if (n === 38 || n === 48) {
      let color = null;
      let j = i + 1;
      if (p[j] === 5 && p.length > j + 1) { color = term256(clamp(p[j + 1])); i = j + 1; }
      else if (p[j] === 2) {
        // 38;2;r;g;b or 38:2::r:g:b (empty colour-space id)
        if (Number.isNaN(p[j + 1]) && p.length > j + 4) j += 1;
        if (p.length > j + 3) { color = `rgb(${clamp(p[j + 1])},${clamp(p[j + 2])},${clamp(p[j + 3])})`; i = j + 3; }
        else i = p.length;
      } else i = p.length;
      if (color) style[n === 38 ? "fg" : "bg"] = color;
    }
  }
  return style;
}

function termCss(style) {
  let fg = style.fg, bg = style.bg;
  if (style.inverse) { fg = style.bg || "var(--term-bg)"; bg = style.fg || "var(--term-fg)"; }
  const css = [];
  if (fg) css.push(`color:${fg}`);
  if (bg) css.push(`background:${bg}`);
  if (style.bold) css.push("font-weight:700");
  if (style.dim) css.push("opacity:.62");
  if (style.italic) css.push("font-style:italic");
  const deco = [style.under ? "underline" : "", style.strike ? "line-through" : ""].filter(Boolean).join(" ");
  if (deco) css.push(`text-decoration:${deco}`);
  if (style.hidden) css.push("visibility:hidden");
  return css.join(";");
}

// v0.9.8.8: file paths on the screen open the file viewer (app.js). The pattern's characters never
// include & < > " ', so a path in already-escaped text is the same text and safe in the attribute.
function termFiles(html) {
  if (typeof UI === "undefined" || !UI.files) return html;
  return html.replace(new RegExp(UI.files.pattern, "gi"), (m, path) => path.split("/").includes("..") ? m
    : `<span class="file-chip term-file" role="button" tabindex="0" data-file="${path}">${path}</span>`);
}

// ANSI text -> one HTML string per line (escaped text in styled spans) plus each line's plain text.
// Styles carry over line ends, as in a terminal.
function ansiLines(text, state = TERM_RESET()) {
  const out = [];
  const style = Object.assign(TERM_RESET(), state);
  for (const raw of String(text || "").split("\n")) {
    let html = "", plain = "";
    const rx = /\x1b\[([0-9;:]*)m/g;
    let last = 0, m;
    const emit = (chunk) => {
      if (!chunk) return;
      plain += chunk;
      const css = termCss(style);
      html += css ? `<span style="${css}">${termFiles(termEscape(chunk))}</span>` : termFiles(termEscape(chunk));
    };
    while ((m = rx.exec(raw))) { emit(raw.slice(last, m.index)); termSgr(style, m[1]); last = rx.lastIndex; }
    emit(raw.slice(last).replace(/\x1b/g, ""));
    out.push({ html, plain });
  }
  return { lines: out, state: style };
}

// How many lines the screen scrolled between two reads: the smallest k > 0 where the old screen's
// lines from k on start the new screen. Agent CLIs keep a fixed prompt box at the bottom, so a match
// of the first 3 lines (at least 2 of them not blank) is enough; 0 = no scroll seen.
function scrolledBy(prev, next) {
  if (!prev.length || !next.length) return 0;
  if (prev.length === next.length && prev.every((x, i) => x === next[i])) return 0;
  for (let k = 1; k < prev.length; k++) {
    const m = Math.min(8, prev.length - k, next.length);
    let matched = 0, content = 0;
    while (matched < m && prev[k + matched] === next[matched]) { if (next[matched].trim()) content++; matched++; }
    if (matched >= Math.min(3, m) && content >= 2) return k;
  }
  return 0;
}

const TERM_MAX_BACK = 2000;
const TERM_POLL_MS = 1000;
const term = { open: null };

function termPrefs() {
  try { return Object.assign({ wrap: false, size: 12 }, JSON.parse(localStorage.getItem("hat-term") || "{}")); }
  catch (_) { return { wrap: false, size: 12 }; }
}
function termSavePrefs(p) { try { localStorage.setItem("hat-term", JSON.stringify(p)); } catch (_) {} }

function terminalButton(agent, { label = true, cls = "" } = {}) {
  if (!agent || agent.in_process || !agent.pane_id && !agent.present) return null;
  return h("button", { class: `btn term-open ${cls}`.trim(), type: "button", title: "Live terminal (read-only)",
    "aria-label": `Live terminal of ${displayName(agent)}`, "data-term": agent.identity,
    on: { click: (e) => { e.preventDefault(); e.stopPropagation(); openTerminal(agent.identity); } } },
    h("span", { class: "term-glyph", "aria-hidden": "true", text: ">_" }), label ? h("span", { text: " Terminal" }) : null);
}

function openTerminal(identity) {
  const target = hashLink("terminal", identity);
  if (location.hash !== target) location.hash = target; else syncTerminal();
}

function closeTerminal() {
  const id = term.open?.identity;
  stopTerminal();
  if (id && location.hash === hashLink("terminal", id)) {
    // Back to where it was opened from (graph, list, question); a direct link falls back to the agent page.
    if ((app.trail || []).length > 1) history.back(); else location.hash = hashLink("agent", id);
  }
}

function stopTerminal() {
  if (!term.open) return;
  clearTimeout(term.open.timer);
  term.open.abort?.abort();
  term.open.root.remove();
  document.body.classList.remove("term-on");
  term.open = null;
}

// Called on every hash change and render: the #/a/<id>/terminal route shows the sheet.
function syncTerminal() {
  const r = route();
  const want = r.section === "terminal" ? r.id : null;
  if (term.open && term.open.identity !== want) stopTerminal();
  if (want && !term.open) startTerminal(want);
  if (term.open) termHeader();
}

function termAgent() { return term.open && (agentById(term.open.identity) || { identity: term.open.identity, name: term.open.identity }); }

function startTerminal(identity) {
  const prefs = termPrefs();
  const s = term.open = { identity, prefs, hash: null, screen: [], back: [], history: null, follow: true, timer: null,
    abort: null, failures: 0 };
  const scroll = h("div", { class: "term-scroll", tabindex: "0", "aria-label": "Terminal output" });
  s.histEl = h("pre", { class: "term-pre term-history", hidden: true });
  s.backEl = h("pre", { class: "term-pre term-back" });
  s.screenEl = h("pre", { class: "term-pre term-screen" });
  scroll.append(s.histEl, s.backEl, s.screenEl);
  s.scroll = scroll;
  s.live = h("button", { class: "btn term-live", type: "button", hidden: true, text: "↓ Live",
    on: { click: () => { s.follow = true; termToBottom(); termHeader(); } } });
  s.status = h("span", { class: "term-status muted small", text: "Connecting…" });
  s.head = h("div", { class: "term-title" });
  s.controls = h("div", { class: "term-controls" });
  s.foot = h("div", { class: "term-foot" });
  s.root = h("div", { id: "terminal", class: "term-sheet", role: "dialog", "aria-modal": "true", "aria-label": "Live terminal" },
    h("div", { class: "term-bar" },
      h("button", { class: "btn quiet term-close", type: "button", "aria-label": "Close terminal", text: "✕",
        on: { click: closeTerminal } }), s.head, s.controls),
    h("div", { class: "term-body" }, scroll, s.live),
    s.foot);
  scroll.addEventListener("scroll", () => {
    const atBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 24;
    if (atBottom !== s.follow) { s.follow = atBottom; termHeader(); }
  }, { passive: true });
  termApplyPrefs();
  document.body.appendChild(s.root);
  document.body.classList.add("term-on");
  termHeader();
  termPoll(true);
}

function termApplyPrefs() {
  const s = term.open;
  s.root.style.setProperty("--term-size", `${s.prefs.size}px`);
  s.root.classList.toggle("term-wrap", !!s.prefs.wrap);
}

function termHeader() {
  const s = term.open; if (!s) return;
  const a = termAgent();
  const fill = (el, ...kids) => el.replaceChildren(...kids.filter((k) => k != null));
  fill(s.head,
    h("span", { class: `dot s-${a.state || "unknown"}`, "aria-hidden": "true" }),
    h("strong", { class: "term-name", text: agentById(s.identity) ? displayName(a) : (a.name || a.identity) }),
    a.runtime ? modelBadge(a) : null);
  const pref = (change) => { Object.assign(s.prefs, change); termSavePrefs(s.prefs); termApplyPrefs(); termHeader(); if (s.follow) termToBottom(); };
  fill(s.controls,
    h("button", { class: "btn quiet", type: "button", "aria-pressed": String(s.follow), title: "Follow the newest output",
      text: s.follow ? "Following" : "Paused", on: { click: () => { s.follow = !s.follow; if (s.follow) termToBottom(); termHeader(); } } }),
    h("button", { class: "btn quiet", type: "button", "aria-pressed": String(!!s.prefs.wrap), text: "Wrap",
      on: { click: () => pref({ wrap: !s.prefs.wrap }) } }),
    h("button", { class: "btn quiet", type: "button", "aria-label": "Smaller text", text: "A−",
      on: { click: () => pref({ size: Math.max(8, s.prefs.size - 1) }) } }),
    h("button", { class: "btn quiet", type: "button", "aria-label": "Larger text", text: "A+",
      on: { click: () => pref({ size: Math.min(20, s.prefs.size + 1) }) } }));
  s.live.hidden = s.follow;
  const agent = agentById(s.identity);
  fill(s.foot, s.status,
    agent && typeof replyButton === "function" ? replyButton(agent) : null,
    agent && typeof openInHerdrButton === "function" ? openInHerdrButton(agent) : null);
}

function termToBottom() { const s = term.open; if (s) s.scroll.scrollTop = s.scroll.scrollHeight; }

async function termPoll(first = false) {
  const s = term.open; if (!s) return;
  clearTimeout(s.timer);
  if (document.hidden) { s.timer = setTimeout(termPoll, TERM_POLL_MS * 2); return; }   // paused while the tab is hidden
  s.abort = new AbortController();
  try {
    const q = new URLSearchParams({ identity: s.identity });
    if (s.hash) q.set("since", s.hash);
    if (first) q.set("history", "1");
    const data = await api(`${UI.api.terminal}?${q}`, { signal: s.abort.signal });
    if (term.open !== s) return;
    s.failures = 0;
    termApply(data);
  } catch (err) {
    if (term.open !== s || err?.name === "AbortError") return;
    s.failures++;
    s.status.textContent = `Not updating: ${err.message || err}`;
  }
  if (term.open === s) s.timer = setTimeout(termPoll, Math.min(10000, TERM_POLL_MS * (1 + s.failures)));
}

function termApply(data) {
  const s = term.open;
  const keepBottom = s.follow;
  if (data.history != null && s.history == null) {
    s.history = data.history;
    s.histEl.hidden = !data.history;
    s.histEl.textContent = data.history ? data.history + "\n" : "";
  }
  if (!data.unchanged && data.screen != null) {
    const parsed = ansiLines(data.screen).lines;
    while (parsed.length && !parsed[parsed.length - 1].plain.trim()) parsed.pop();
    const k = scrolledBy(s.screen.map((l) => l.plain), parsed.map((l) => l.plain));
    if (k) {
      s.back.push(...s.screen.slice(0, k));
      if (s.back.length > TERM_MAX_BACK) s.back.splice(0, s.back.length - TERM_MAX_BACK);
      s.backEl.innerHTML = s.back.map((l) => l.html).join("\n") + (s.back.length ? "\n" : "");
    }
    s.screen = parsed;
    s.screenEl.innerHTML = parsed.map((l) => l.html).join("\n");
    s.hash = data.hash;
  }
  const when = new Date((data.read_at || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  s.status.textContent = data.note || `Read-only · live · ${when}${data.redactions ? ` · ${data.redactions} redacted` : ""}`;
  if (keepBottom) termToBottom();
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && term.open && !app.composer && !app.fileView) closeTerminal(); });
document.addEventListener("visibilitychange", () => { if (term.open && !document.hidden) termPoll(); });

// app.js may have drawn its first page before this file loaded: open the sheet for a direct link now.
if (typeof app !== "undefined" && app.view) syncTerminal();

if (typeof module !== "undefined") module.exports = { ansiLines, scrolledBy, termSgr, term256, termFiles, TERM_PALETTE };
