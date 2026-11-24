// Herdr dashboard: workspaces first, then their agents, then one agent.
// Read-only towards agents. All text from the API is set with textContent.
"use strict";

let STATES = ["needs_you", "working", "done", "waiting", "parked", "unknown"];
const STATE_LABEL = {
  needs_you: "Needs you", working: "Working", done: "Done, not reviewed",
  waiting: "Waiting", parked: "Parked", unknown: "Unclear",
};
const STATE_SHORT = { needs_you: "need you", working: "working", done: "done", waiting: "waiting", parked: "parked", unknown: "unclear" };
const ALERT = ["approval", "question", "blocked", "failure", "completion", "stall"];
const NEEDS = ["approval", "question", "blocked", "failure"];
const POLL_MS = 10000;
const DETAIL_MS = 15000;

const app = {
  view: null, csrf: "", lastOk: 0, error: null, renderedKey: "",
  details: {}, outputs: {}, chooser: null, timer: null,
  trail: [location.hash || hashLink('home')], scroll: {}, ackErrors: {}, audit: null,  // trail: in-app navigation, for "back"
  notifySetup: null, ntfyTest: null, summarizing: {},
  composer: null, asking: {}, markingRead: new Set(),
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- helpers

function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key === "on") {
      node.uiHandlers = value;
      for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn);
    }
    else if (key === "style") for (const [prop, v] of Object.entries(value)) node.style.setProperty(prop, v);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Patch the same session's reading surface in place. Native details.open,
// focused controls, selections and nested scroll containers belong to the
// reader; generated content and action handlers belong to the latest data.
function readingKey(node) {
  return node.nodeType === Node.ELEMENT_NODE ? node.getAttribute("data-reading-key") : null;
}

function sameReadingNode(a, b) {
  return a && a.nodeType === b.nodeType && a.nodeName === b.nodeName && readingKey(a) === readingKey(b);
}

function patchReadingNode(node, fresh) {
  // A live update must never touch the active editing surface.
  if (node === document.activeElement && (node.matches?.("input, textarea, select") || node.isContentEditable)) return;
  if (node.nodeType === Node.TEXT_NODE) {
    if (node.data !== fresh.data) node.data = fresh.data;
    return;
  }
  const selected = node.tagName === "SELECT" ? node.value : null;
  if (node.tagName === "INPUT" && ["checkbox", "radio"].includes(node.type)) node.checked = fresh.checked;
  const readerAttribute = (name) => (node.tagName === "DETAILS" && name === "open")
    || (["INPUT", "TEXTAREA", "SELECT", "OPTION"].includes(node.tagName) && ["value", "selected"].includes(name));
  for (const attr of [...node.attributes]) {
    if (!readerAttribute(attr.name) && !fresh.hasAttribute(attr.name)) node.removeAttribute(attr.name);
  }
  for (const attr of fresh.attributes) {
    if (!readerAttribute(attr.name) && node.getAttribute(attr.name) !== attr.value) node.setAttribute(attr.name, attr.value);
  }
  for (const [event, fn] of Object.entries(node.uiHandlers || {})) node.removeEventListener(event, fn);
  node.uiHandlers = fresh.uiHandlers || {};
  for (const [event, fn] of Object.entries(node.uiHandlers)) node.addEventListener(event, fn);
  const old = [...node.childNodes], used = new Set();
  [...fresh.childNodes].forEach((child, index) => {
    const key = readingKey(child);
    const candidate = key ? old.find((n) => !used.has(n) && sameReadingNode(n, child)) : node.childNodes[index];
    if (candidate && !used.has(candidate) && sameReadingNode(candidate, child)) {
      used.add(candidate);
      if (node.childNodes[index] !== candidate) node.insertBefore(candidate, node.childNodes[index] || null);
      patchReadingNode(candidate, child);
    } else {
      node.insertBefore(child, node.childNodes[index] || null);
    }
  });
  for (const child of old) if (!used.has(child)) child.remove();
  if (selected !== null && [...node.options].some((o) => o.value === selected)) node.value = selected;
}

function editingCounterpart(current, fresh, active) {
  if (!sameReadingNode(current,fresh)) return false;
  if (current===active) return ["aria-label","data-setting","type","name"].every(key=>current.getAttribute(key)===fresh.getAttribute(key));
  const children=[...current.childNodes], index=children.findIndex(n=>n===active || n.contains?.(active));
  if(index<0) return false;
  const child=children[index],key=readingKey(child);
  const next=key?[...fresh.childNodes].find(n=>readingKey(n)===key):fresh.childNodes[index];
  return !!next && editingCounterpart(child,next,active);
}

function updateReadingView(container, fresh) {
  const active=document.activeElement;
  if (active && container.contains(active) && active.matches("input, textarea, select, [contenteditable=true]") && !editingCounterpart(container.firstChild,fresh,active)) {
    app.renderDeferred=true;app.renderDeferredForce=true;return;
  }
  const current = container.firstChild;
  if (!sameReadingNode(current, fresh)) { container.replaceChildren(fresh); return; }
  const scroll = [container, ...container.querySelectorAll("*")]
    .filter((node) => node.scrollTop || node.scrollLeft)
    .map((node) => [node, node.scrollTop, node.scrollLeft]);
  const x = window.scrollX, y = window.scrollY;
  patchReadingNode(current, fresh);
  for (const [node, top, left] of scroll) { node.scrollTop = top; node.scrollLeft = left; }
  window.scrollTo(x, y);
}

function duration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
  return `${Math.floor(s / 86400)}d`;
}
const now = () => Date.now() / 1000;
const ago = (ts) => (ts ? duration(now() - ts) : "?");

function clock(ts) {
  if (!ts) return "?";
  const d = new Date(ts * 1000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString() ? time : `${UI.ask_items.weekdays[d.getDay()]} ${time}`;
}

function since(agent) {
  if (!agent.status_since) return "";
  return (agent.since_known ? "" : "≥") + ago(agent.status_since);
}

function lastActivity(agent) {
  if (agent.last_activity_at) return `active ${ago(agent.last_activity_at)} ago`;
  return `no activity seen since ${clock(agent.first_seen)}`;
}

function displayName(agent) {
  return agent.name || `${agent.agent || "agent"} ${String(agent.pane_id || "").split(":")[1] || ""}`.trim();
}

function initials(agent) {
  const name = agent.name || agent.agent || "?";
  const parts = name.split(/[-_\s.]+/).filter(Boolean);
  return ((parts[0] || "?")[0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

// v0.9.7.4: the model in use ("Sol 6.2", "Fable 5.2") where the CC / CX service badge was; the
// service by name ("Claude", "Codex") when no model is recorded. Text from the server (agent_runtime.badge).
function modelBadge(agent, extra = "") {
  const b = agent?.runtime?.badge;
  if (!b || !b.text) return null;
  // v0.9.8.6: one chip for every service, "Opus 5.5 · high" / "Sol 6.2 · high". Without a recorded
  // model it names the service in the same chip, dashed (is-service): unknown, not a different look.
  return h("span", { class: `model-chip${b.from === "service" ? " is-service" : ""}${extra ? " " + extra : ""}`,
    title: b.title || b.label || b.text, text: b.label || b.text });
}

function unknown(text = "unknown") {
  return h("span", { class: "unknown-val", text });
}

// ---------------------------------------------------------------- markdown (v0.9.6)
// A safe subset for agent text: bold, italic, code, lists, tables as plain rows,
// links as their text. Builds DOM nodes; agent text never reaches innerHTML.
// Long text is cut only at a word boundary, with a "More" control.

const MD_MAX = 40000;
const MD_INLINE = /(`+)([^`\n]+?)\1|\*\*(\S(?:[^\n]*?\S)?)\*\*|__(\S(?:[^\n]*?\S)?)__|~~(\S(?:[^\n]*?\S)?)~~|\[([^\]\n]+)\]\([^)\n]*\)|<(https?:[^>\s]+)>|(?<![\w*])\*(?![\s*])([^\n*]*?[^\s*])\*(?!\w)|(?<![\w_])_(?![\s_])([^\n_]*?[^\s_])_(?![\w_])/g;

// Inline text -> segments [{text, b, i, c}]; markers are dropped, never shown.
function mdInline(text, marks = {}, depth = 0) {
  text = String(text ?? "");
  if (depth > 4) return [{ ...marks, text }];
  const out = [];
  let last = 0;
  for (const m of text.matchAll(new RegExp(MD_INLINE.source, "g"))) {
    if (m.index > last) out.push({ ...marks, text: text.slice(last, m.index) });
    if (m[1]) out.push({ ...marks, c: true, text: m[2] });
    else if (m[3] ?? m[4]) out.push(...mdInline(m[3] ?? m[4], { ...marks, b: true }, depth + 1));
    else if (m[5] ?? m[6]) out.push(...mdInline(m[5] ?? m[6], marks, depth + 1));   // strike, link: the text only
    else if (m[7]) out.push({ ...marks, text: m[7] });
    else out.push(...mdInline(m[8] ?? m[9], { ...marks, i: true }, depth + 1));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ ...marks, text: text.slice(last) });
  // An unclosed ** is a marker too: never show it (__ stays: __init__.py).
  return out.map((s) => s.c ? s : { ...s, text: s.text.replace(/\*\*/g, "") }).filter((s) => s.text);
}

// Text -> blocks: p, h, li, quote, code, row (a table row; the separator row is dropped).
function mdBlocks(text) {
  const blocks = [];
  let para = null, fence = null;
  const flush = () => { if (para) blocks.push({ type: "p", text: para.join(" ") }); para = null; };
  for (const raw of String(text ?? "").slice(0, MD_MAX).replace(/\r\n?/g, "\n").split("\n")) {
    if (fence) {
      if (/^\s*(```|~~~)/.test(raw)) { blocks.push({ type: "code", text: fence.join("\n") }); fence = null; }
      else fence.push(raw);
      continue;
    }
    const line = raw.trimEnd();
    let m;
    if (/^\s*(```|~~~)/.test(line)) { flush(); fence = []; continue; }
    if (!line.trim()) { flush(); continue; }
    if ((m = line.match(/^\s{0,3}#{1,6}\s+(.*?)[\s#]*$/))) { flush(); blocks.push({ type: "h", text: m[1] }); continue; }
    if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flush();
      if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) { const head = blocks.at(-1); if (head?.type === "row") head.head = true; continue; }
      blocks.push({ type: "row", cells: line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()) });
      continue;
    }
    if ((m = line.match(/^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/))) {
      flush();
      blocks.push({ type: "li", ordered: /\d/.test(m[2]), n: parseInt(m[2], 10) || null,
        depth: Math.min(3, Math.floor(m[1].replace(/\t/g, "  ").length / 2)), text: m[3] });
      continue;
    }
    if ((m = line.match(/^\s*>\s?(.*)$/))) { flush(); blocks.push({ type: "quote", text: m[1] }); continue; }
    if (!para && blocks.at(-1)?.type === "li" && /^\s+\S/.test(raw)) { blocks.at(-1).text += ` ${line.trim()}`; continue; }
    (para ||= []).push(line.trim());
  }
  if (fence) blocks.push({ type: "code", text: fence.join("\n") });
  flush();
  return blocks;
}

function mdSegments(b) {
  if (b.type === "code") return [{ c: true, text: b.text }];
  if (b.type === "row") return b.cells.flatMap((cell, i) => [...(i ? [{ text: " · " }] : []), ...mdInline(cell, b.head ? { b: true } : {})]);
  return mdInline(b.text);
}

// Cut segments to about `budget` characters, at a word boundary, and add "…".
function mdCut(segs, budget) {
  const full = segs.map((s) => s.text).join("");
  if (full.length <= budget) return segs;
  let at = full.slice(0, budget + 1).search(/\s\S*$/);
  if (at <= 0) { const next = full.slice(budget).search(/\s/); at = next < 0 ? full.length : budget + next; }
  const out = [];
  let used = 0;
  for (const s of segs) {
    if (used >= at) break;
    out.push({ ...s, text: s.text.slice(0, at - used) });
    used += s.text.length;
  }
  if (out.length) out[out.length - 1].text = out[out.length - 1].text.trimEnd();
  out.push({ text: "…" });
  return out;
}

function mdSpan(segs, files = false) {
  return segs.map((s) => {
    if (files && s.c && isFilePath(s.text)) return fileChip(s.text.trim(), { code: true });
    const nodes = files && !s.c ? fileNodes(s.text) : null;
    let node = s.c ? h("code", { text: s.text }) : nodes?.length > 1 ? h("span", {}, nodes) : nodes ? nodes[0] : document.createTextNode(s.text);
    if (s.i) node = h("em", {}, node);
    if (s.b) node = h("strong", {}, node);
    return node;
  });
}

function mdKey(text) {
  let x = 0;
  for (let i = 0; i < text.length; i++) x = (x * 31 + text.charCodeAt(i)) | 0;
  return `${text.length}:${x >>> 0}`;
}

// A block of agent text. With `limit` and `place`, longer text is cut at a word
// boundary and a More button opens it in full; the choice survives live refresh.
function mdBlock(text, { limit = 0, place = "", cls = "" } = {}) {
  const key = place ? `${place}:${mdKey(String(text ?? ""))}` : null;
  const open = !!key && !!app.mdOpen?.has(key);
  let budget = limit && key && !open ? limit : Infinity, cut = false;
  const out = [];
  let list = null;
  for (const b of mdBlocks(text)) {
    if (budget <= 0) { cut = true; break; }
    let segs = mdSegments(b);
    const length = segs.reduce((n, s) => n + s.text.length, 0);
    if (length > budget) { segs = mdCut(segs, budget); cut = true; }
    budget -= length;
    const inline = mdSpan(segs, true);
    if (b.type === "li") {
      const tag = b.ordered ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag, { class: "md-list" }); out.push(list); }
      list.append(h("li", { class: b.depth ? `md-d${b.depth}` : null, value: b.ordered && b.n ? b.n : null }, inline));
    } else {
      list = null;
      out.push(b.type === "h" ? h("p", { class: "md-h" }, h("strong", {}, inline))
        : b.type === "quote" ? h("blockquote", { class: "md-quote" }, inline)
        : b.type === "code" ? h("pre", { class: "md-code" }, h("code", { text: segs.map((x) => x.text).join("") }))
        : b.type === "row" ? h("p", { class: `md-row${b.head ? " md-row-head" : ""}` }, inline)
        : h("p", {}, inline));
    }
    if (cut) break;
  }
  const more = (cut || open) && key ? h("button", { class: "btn quiet md-more", type: "button", "aria-expanded": String(open),
    text: open ? UI.ask_items.labels.less : UI.ask_items.labels.more,
    on: { click: (e) => { e.preventDefault(); e.stopPropagation(); app.mdOpen ||= new Set();
      if (open) app.mdOpen.delete(key); else app.mdOpen.add(key); render(true); } } }) : null;
  return h("div", { class: `md${cls ? ` ${cls}` : ""}` }, out, more);
}

// One line of agent text (rows, quotes, question text): markers rendered, block
// marks dropped, line breaks as spaces; cut at a word boundary with "…".
function mdLine(text, limit = 0, files = false) {
  const flat = String(text ?? "").slice(0, MD_MAX).split(/\n/).map((l) => l.replace(/^\s*(#{1,6}\s+|[-*+]\s+|\d{1,3}[.)]\s+|>\s?|\|)/, "").replace(/\|\s*$/, "").trim())
    .filter((l) => l && !/^[\s:|-]+$/.test(l) && !/^(```|~~~)/.test(l)).join(" ").replace(/\s*\|\s*/g, " · ");
  const segs = mdInline(flat);
  return mdSpan(limit ? mdCut(segs, limit) : segs, files);
}

// Plain text of a markdown string (aria labels, titles).
function mdPlain(text) {
  return mdInline(String(text ?? "").replace(/\s+/g, " ")).map((s) => s.text).join("");
}

// ---------------------------------------------------------------- file paths (v0.9.8.8)
// A path an agent wrote (team-registry/docs/working-styles.md, ~/Work/x/NOTES.txt) becomes a chip
// that opens the read-only file viewer. One pattern for page, terminal and tests (UI.files.pattern).
// Never a path with "..": the server refuses those anyway.
function fileRx() { return new RegExp(UI.files.pattern, "gi"); }

function isFilePath(text) {
  const t = String(text ?? "").trim();
  const m = t.match(new RegExp(`^(?:${UI.files.pattern})$`, "i"));
  return !!m && !t.split("/").includes("..");
}

function fileName(path) { return String(path).split("/").filter(Boolean).pop() || String(path); }

function fileChip(path, { code = false, label = null } = {}) {
  return h(code ? "code" : "span", { class: `file-chip${code ? " is-code" : ""}`, role: "button", tabindex: "0", "data-file": path,
    title: UI.files.labels.open.replace("{name}", fileName(path)), text: label ?? path });
}

// Text -> text nodes and file chips.
function fileNodes(text) {
  text = String(text ?? "");
  const out = [];
  let last = 0;
  for (const m of text.matchAll(fileRx())) {
    const path = m[1];
    if (path.split("/").includes("..")) continue;
    if (m.index > last) out.push(document.createTextNode(text.slice(last, m.index)));
    out.push(fileChip(path));
    last = m.index + m[0].length;
  }
  if (last < text.length || !out.length) out.push(document.createTextNode(text.slice(last)));
  return out;
}

function apiPath(name, id) {
  return UI.api[name].replace("{id}", encodeURIComponent(id));
}

function hashLink(name, id = "") {
  return UI.hashes[name].replace("{id}", encodeURIComponent(id));
}

// One action component used by all views. A phone never creates its markup.
function openInHerdrButton(agent) {
  // Focus needs a Herdr pane; in-process subagents have none.
  if (!app.view?.features?.[UI.features.desktop_focus] || !agent?.present || !agent.pane_id) return null;
  return h("button", { class: "btn open-in-herdr", type: "button", text: UI.labels.open_in_herdr,
    "data-focus-identity": agent.identity, on: { click: async (event) => {
      event.preventDefault();
      event.stopPropagation();
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await api(UI.api.focus, { method: "POST", body: { identity: agent.identity } });
        location.hash = result.hash || hashLink("conversation", agent.identity);
      } catch (err) { flash(`${UI.labels.open_in_herdr}: ${err.message}`); }
      finally { button.disabled = false; }
    } } });
}

async function desktopNavigation() {
  if (!app.view?.features?.[UI.features.desktop_focus]) return;
  try {
    const next = await api(UI.api.desktop_navigation);
    if (next.sequence && next.sequence !== app.desktopNavigationSequence) {
      app.desktopNavigationSequence = next.sequence;
      location.hash = next.hash;
    }
  } catch (_) { /* the normal freshness indicator reports connection errors */ }
}

function evidenceLines(agent) {
  const e = agent.evidence || {};
  const b = agent.state_basis || {};
  const lines = [];
  if (agent.session_collision) lines.push("Shared native session: pane identities are separate; transcript attribution is ambiguous.");
  if (e.herdr_status) lines.push(`Herdr status: ${e.herdr_status}`);
  if (e.rule) lines.push(`Matched screen rule: ${e.rule}`);
  if (e.working_minutes !== undefined)
    lines.push(`Working for ${e.working_since_known ? "" : "at least "}${e.working_minutes} min (stall limit ${e.threshold_minutes})`);
  if (e.output_unchanged_minutes !== undefined)
    lines.push(`Same screen in ${e.output_checks || "several"} checks over ${e.output_unchanged_minutes} min (every ${e.output_checked_every_seconds} s; ${e.output_source || "visible screen"})`);
  if (e.last_check_at) lines.push(`Last check ${ago(e.last_check_at)} ago`);
  if (e.output_check) lines.push(`Output check ${e.output_check}`);
  if (b.quiet_minutes !== undefined)
    lines.push(`Quiet for ${b.quiet_known ? "" : "at least "}${b.quiet_minutes} min; parked after ${b.parked_after_minutes} min`);
  if (e.reason) lines.push(`Reason: ${e.reason}${e.last_status ? ` (was ${e.last_status})` : ""}`);
  if (e.note) lines.push(e.note);
  lines.push(`Confidence: ${agent.confidence}`);
  if (agent.seen_late) lines.push("Seen late: it happened while the collector or Herdr was offline");
  if (agent.missed_changes) lines.push(`${agent.missed_changes} status change(s) happened while the collector was not looking`);
  return lines;
}

// ---------------------------------------------------------------- API

async function api(path, options = {}) {
  const init = { credentials: "same-origin", headers: {}, ...options };
  if (init.body instanceof Blob) {
    init.headers["Content-Type"] = init.body.type || "application/octet-stream";   // an account image
  } else if (init.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  const write = init.method === "POST" || init.method === "DELETE";
  if (write) init.headers["X-CSRF-Token"] = app.csrf;
  let response = await fetch(path, init);
  if (response.status === 403 && write) {
    // A collector restart replaces the desktop CSRF token. Retry once with
    // the current authenticated session; an actual refusal still reaches UI.
    const session = await fetch(UI.api.session, {credentials:"same-origin"});
    const current = await session.json().catch(()=>({}));
    if (session.ok && current.csrf) {
      app.csrf=current.csrf;init.headers["X-CSRF-Token"]=app.csrf;
      response=await fetch(path,init);
    }
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    showLogin();
    throw new Error("login required");
  }
  if (!response.ok) {
    const error=new Error(data.error || `HTTP ${response.status}`);error.status=response.status;throw error;
  }
  return data;
}

async function saveSetting(key, value) { return saveSettings({ [key]: value }); }

async function saveSettings(body) {
  const before = { ...app.view?.settings };
  try { await api(UI.api.settings, { method: "POST", body }); }
  catch (err) { flash(`Could not save: ${err.message}`); return false; }
  if (Object.keys(body).some(k => k.startsWith("watch."))) app.chooser = null;
  await refresh(true);
  const changed = Object.keys(app.view?.settings || {}).filter(k => before[k] !== app.view.settings[k]);
  app.savedSettings = { keys: [...new Set([...Object.keys(body), ...changed, ...(Object.hasOwn(body, "summaries.enabled") ? ["summaries.mode"] : [])])], changed, at: Date.now() };
  render(true);
  clearTimeout(saveSettings.timer);
  saveSettings.timer = setTimeout(() => {
    app.savedSettings = null;
    document.querySelectorAll(".save-feedback").forEach(el => el.remove());
    document.querySelectorAll(".just-changed").forEach(el => el.classList.remove("just-changed"));
  }, 4000);
  return true;
}

function decorateSavedControls(root) {
  const saved = app.savedSettings;
  if (!saved || Date.now() - saved.at > 4000) return;
  for (const el of root.querySelectorAll("[data-setting]")) {
    const key = el.getAttribute("data-setting");
    if (!saved.keys.includes(key)) continue;
    const row = el.closest(".setting, .tier-row") || el.parentElement;
    if (!row.querySelector(":scope > .save-feedback")) row.appendChild(h("span", { class: "save-feedback", role: "status", text: "Saved" }));
    row.classList.toggle("just-changed", saved.changed.includes(key));
  }
}

function flash(text) {
  const banner = $("banner");
  banner.textContent = text;
  banner.hidden = false;
  clearTimeout(flash.timer);
  flash.timer = setTimeout(() => { banner.hidden = true; }, 5000);
}

// ---------------------------------------------------------------- login

function showLogin() {
  stopPolling();
  $("login").hidden = false;
  $("app").hidden = true;
  document.querySelector(".topbar").hidden = true;
}

async function login(event) {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    const data = await api("/login", { method: "POST", body: { passphrase: $("passphrase").value } });
    app.csrf = data.csrf;
    $("passphrase").value = "";
    start();
  } catch (err) {
    $("login-error").textContent = err.message === "login required" ? "Wrong passphrase." : err.message;
  }
}

async function logout() {
  try { await api("/logout", { method: "POST" }); } catch (_) { /* already gone */ }
  app.csrf = "";
  showLogin();
}

// ---------------------------------------------------------------- freshness

// One wording for stale discovery, top bar and graph chip alike.
function discoveryStale(herdr) {
  return !!(herdr.reconcile_error || ("last_reconcile" in herdr && (herdr.last_reconcile == null || Date.now() / 1000 - herdr.last_reconcile > 90)));
}

function discoveryStaleText(herdr) {
  const last = herdr.last_reconcile ? `last check ${duration(Math.max(0, Date.now() / 1000 - herdr.last_reconcile))} ago` : "no check yet";
  return `Agent list may be out of date (${last})`;
}

// Short freshness for the top bar and the status header: "5s ago", with a word
// when stale or offline. The long wording lives in the status header.
function freshInfo() {
  if (app.error && app.lastOk) return { cls: "down", text: `offline · ${clock(app.lastOk / 1000)}`,
    title: `Can't reach the laptop · last update ${clock(app.lastOk / 1000)}` };
  if (!app.view) return { cls: "", text: "Connecting…", title: "" };
  const herdr = app.view.collector.herdr;
  if (!herdr.connected) return { cls: "stale", text: "Herdr offline", title: `Herdr not reachable · last contact ${clock(herdr.last_contact)}` };
  const ago = `${duration((Date.now() - app.lastOk) / 1000)} ago`;
  if (discoveryStale(herdr)) return { cls: "stale", text: `stale · ${ago}`, title: herdr.reconcile_error || discoveryStaleText(herdr) };
  return { cls: "", text: ago, title: `Live · discovery ${clock(herdr.last_reconcile)}` };
}

function watchedLiveAgents(view) {
  return (view?.agents || []).filter((a) => a.present && !a.folded);
}

function agentWorkCounts(view) {
  const agents = watchedLiveAgents(view);
  return {
    total: agents.length,
    working: agents.filter((a) => a.state === "working").length,
    needs: agents.filter((a) => agentTier(a) === "waiting_on_you" || a.state === "needs_you").length,
  };
}

function overallAgentStatus(view, fresh) {
  if (fresh.cls === "down") return "down";
  if (fresh.cls === "stale") return "stale";
  const counts = agentWorkCounts(view);
  if (counts.needs) return "needs";
  if (counts.working) return "working";
  return "idle";
}

function agentMeter(counts) {
  const total = counts.total, working = counts.working, needs = counts.needs;
  const idle = Math.max(0, total - working - needs);
  const segs = [];
  if (working) segs.push(h("span", { class: "seg s-working", style: { "flex-grow": String(working), "flex-shrink": "0", "flex-basis": "0" } }));
  if (needs) segs.push(h("span", { class: "seg s-needs", style: { "flex-grow": String(needs), "flex-shrink": "0", "flex-basis": "0" } }));
  if (idle) segs.push(h("span", { class: "seg s-idle", style: { "flex-grow": String(idle), "flex-shrink": "0", "flex-basis": "0" } }));
  const title = total
    ? `${working} of ${total} agents working${needs ? ` · ${needs} need you` : ""}`
    : "No live agents";
  return h("span", { class: "agent-meter", title, "aria-label": title },
    h("span", { class: "agent-meter-track", "aria-hidden": "true" }, ...segs),
    h("span", { class: "agent-meter-count", text: `${working}/${total}` }));
}

function fillFreshSlot(el, info, counts, status) {
  if (!el) return;
  el.classList.remove("stale", "down", "is-working", "is-needs", "is-idle");
  if (status === "down") el.classList.add("down");
  else if (status === "stale") el.classList.add("stale");
  else el.classList.add(`is-${status}`);
  const title = [counts.total ? `${counts.working} of ${counts.total} agents working` : "No live agents", info.title].filter(Boolean).join(" · ");
  el.title = title;
  el.replaceChildren(
    h("span", { class: "fresh-pip", "aria-hidden": "true" }),
    agentMeter(counts),
    h("span", { class: "fresh-ago", text: info.text }),
  );
}

function renderFresh() {
  renderReplyDelivery();
  syncVisualsChrome();
  if (typeof bindVisualThumbs === "function") bindVisualThumbs();
  const info = freshInfo();
  const counts = agentWorkCounts(app.view);
  const status = overallAgentStatus(app.view, info);
  for (const slot of document.querySelectorAll('[data-quota-chip]')) renderQuotaDials(slot);
  for (const el of [$("fresh"), ...document.querySelectorAll(".status-header .sh-fresh")]) {
    fillFreshSlot(el, info, counts, status);
  }
}

// ---------------------------------------------------------------- status header
// First-glance answer on Graph and List (PH-2): calm "Nothing needs you", or a red,
// tappable "N waiting on you". Working/total lives in the freshness slot (pip + meter).
// Caution lines for stale discovery, an unreachable laptop or Herdr, and paused phone alerts.
function statusHeader(view, { fresh = false } = {}) {
  const items = attentionItems(view);
  const you = items.filter((a) => agentTier(a) === "waiting_on_you");
  const herdr = view.collector.herdr;
  const warn = app.error ? `Can't reach the laptop · last update ${clock(app.lastOk / 1000)}`
    : !herdr.connected ? `Herdr not reachable · last contact ${clock(herdr.last_contact)}`
    : discoveryStale(herdr) ? discoveryStaleText(herdr) : null;
  const paused = (view.settings || {})["presence.mode"] === "never";
  const target = hashLink("questions");
  const body = [
    icon(you.length ? "warn" : "check", "sh-icon"),
    h("span", { class: "sh-main" },
      h("span", { class: "sh-title", text: you.length ? `${you.length} waiting on you` : "Nothing needs you" }),
      you[0]?.question ? h("span", {class:"sh-question",text:you[0].question.reason + " “" + you[0].question.text + "”"}) : null,
      fresh ? h("span", { class: "sh-fresh fresh", role: "status" }) : null,
      warn ? h("span", { class: "sh-warn", title: herdr.reconcile_error || null, text: warn }) : null,
      paused ? h("a", { class: "sh-paused", href: hashLink('settings'), text: "Phone alerts paused" }) : null),
    you.length ? icon("chev", "sh-chev") : null,
  ];
  const cls = `status-header ${you.length ? "urgent" : "calm"}${warn ? " stale" : ""}`;
  return you.length
    ? h("a", { class: cls, href: target, "aria-label": `${you.length} waiting on you. Open questions for you.` }, body)
    : h("div", { class: cls, role: "status" }, body);
}

// ---------------------------------------------------------------- routing

function route() {
  const legacy = location.hash.match(/^#event-(\d+)$/);
  if (legacy) history.replaceState(null, "", hashLink("notification", legacy[1]));
  const hash = location.hash || UI.hashes.home;
  for (const [key, template] of Object.entries(UI.hashes)) {
    const pattern = template.split("{id}").map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("([^/]+)");
    const match = hash.match(new RegExp("^" + pattern + "$"));
    if (match) return { name: UI.views[key], id: match[1] ? decodeURIComponent(match[1]) : null,
      section: AGENT_SECTIONS.includes(key) ? key : null };
  }
  return { name: "graph", id: null };
}

// Graph · List · Notifications · Settings: bottom tab bar under 900 px, top bar above.
function updateTabs(r) {
  const current = r.name === "workspace" || r.name === "agent" ? null : r.name;
  for (const tab of document.querySelectorAll("#tabbar .tab"))
    tab.setAttribute("aria-current", tab.dataset.tab === current ? "page" : "false");
}

// A text field (or select) in the page is being used: a poll must not rebuild it.
function typingInPage() {
  const el = document.activeElement;
  if (!el || el.closest("[hidden]")) return false;
  if (el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable) return true;
  return el.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "reset", "range"].includes(el.type);
}

function flushDeferredRender() {
  if (!app.renderDeferred || typingInPage() || selectionSource()) return;
  app.renderDeferred = false;
  const force=!!app.renderDeferredForce;app.renderDeferredForce=false;
  render(force);
  if (app.composer) renderComposer();
}
document.addEventListener("focusout", () => setTimeout(flushDeferredRender, 0));

function render(force = false) {
  if (!app.view) return;
  const r = route();
  const sameRoute = app.renderedKey.startsWith(`${r.name}:${r.id || ""}:`);
  if (sameRoute && ((!force && typingInPage()) || selectionSource())) {
    app.renderDeferred = true; return; // runs when focus or the transcript selection ends
  }
  document.body.classList.toggle("graph-mode", r.name === "graph");
  updateTabs(r);
  if (r.name === "graph") {
    // The graph shell is built once and then updated in place: no remounts.
    const main = $("app");
    if (!graph.shell || !main.contains(graph.shell.root)) main.replaceChildren(buildGraphShell());
    app.renderedKey = `graph:${r.id || ""}:`;
    app.renderedRoute = "graph";
    graphRefresh(force);
    if (app.graphRegion) { const id = app.graphRegion; app.graphRegion = null; showRegion(id); }
    renderFresh();
    if(app.composer) renderComposer();
    if (typeof syncTerminal === "function") syncTerminal();
    return;
  }
  const key = `${r.name}:${r.id || ""}:${app.view.version}:${app.details[r.id]?.generated_at || ""}:${app.chooser ? 1 : 0}:${JSON.stringify(app.outputs[r.id] || "")}:${graph.size}:${r.name === "notifications" ? `${app.notifications?.at}:${app.notifGone}` : ""}`;
  if (!force && key === app.renderedKey) return;
  const routeChanged = !app.renderedKey.startsWith(`${r.name}:${r.id || ""}:`);
  app.renderedKey = key;
  const main = $("app");
  const views = { overview: renderOverview, workspace: renderWorkspace, agent: renderAgent, settings: renderSettings, notifications: renderNotifications, quotas: renderQuotas, visuals: renderVisualsHub, questions: () => h("div", {}, h("a", {class:"back",href:hashLink("home"),text:"Back"}), questionsBlock(app.view, false)) };
  const content = views[r.name](r);
  if (["overview", "agent", "settings", "quotas", "questions", "notifications", "visuals"].includes(r.name) && !routeChanged) updateReadingView(main, content);
  else main.replaceChildren(content);
  if (routeChanged) window.scrollTo(0, app.scroll[hashKey()] || 0);
  if (r.name === "agent" && r.section) openAgentSection(r); else app.sectionShown = null;
  app.renderedRoute = r.name;
  decorateSavedControls(main);
  if (r.name === "notifications") focusNotification(r);
  renderFresh();
  if(app.composer) renderComposer();
  if (typeof syncTerminal === "function") syncTerminal();
}


// One quota line from the server (quotas.summary): header chip, List row and Quotas page agree.
function quotaSummary() {
  return app.view?.quotas?.summary || { label: app.view?.quotas?.header?.label || "Quotas", level: "low", hot: 0 };
}

function quotaName(account) {
  return account.label && account.provider_name && account.label !== account.provider_name
    ? `${account.provider_name} · ${account.label}`
    : (account.label || account.provider_name || account.provider);
}

function quotaPct(win) {
  return Math.max(0, Math.min(100, Number.isFinite(win.used_pct) ? win.used_pct : Math.round((win.percent || 0) * 100)));
}

function worstWindow(account) {
  return (account.windows || []).reduce((worst, w) => (!worst || quotaPct(w) > quotaPct(worst) ? w : worst), null);
}

// One line per account (SE-12): name, every window's %, a meter for the fullest one,
// its reset, and the record's age. Near the limit is caution; red only at the wall.
function quotaCard(account) {
  const stale = account.stale === true;
  const worst = worstWindow(account);
  const top = worst ? quotaPct(worst) : 0;
  // Levels come from the server: Alex's warning levels per account (quotas.py quota_level).
  const wall = account.level === "wall";
  const alarm = account.level === "high";
  const name = quotaName(account);
  const classes = ["quota-row", stale ? "stale" : "", wall ? "wall" : "", alarm ? "alarm" : ""].filter(Boolean).join(" ");
  const windows = (account.windows || []).map((w) => `${w.title || w.label || "Limit"} ${quotaPct(w)}%`).join(" · ");
  const reset = worst ? (worst.expired ? "window reset" : (worst.resets_label ? `resets ${worst.resets_label}` : "")) : "";
  const age = [stale ? (account.status || "stale") : null, account.fetched_age_label || null].filter(Boolean).join(" · ");
  return h("article", { class: classes, "data-quota-id": account.id, "aria-label": name,
                        title: [account.plan, account.active ? "active" : null, account.note].filter(Boolean).join(" · ") || null },
    h("span", { class: "quota-name", text: name }),
    worst ? h("div", { class: `quota-meter q-${worst.level || account.level || "low"}${stale ? " is-stale" : ""}`, role: "img",
                       "aria-label": `${worst.title || worst.label} ${top} percent used` }, h("i", { style: { width: `${top}%` } }))
      : null,
    h("span", { class: "quota-pct", text: worst ? `${top}%` : "–" }),
    h("span", { class: "quota-line" }, windows || account.note || "No quota meters", reset ? ` · ${reset}` : "", age ? ` · ${age}` : ""));
}

// Collapsed to one row below the workspaces: the accounts near or at a limit by name, the rest counted.
function quotaStrip(view) {
  const accounts = view.quotas && view.quotas.accounts;
  if (!accounts || !accounts.length) return null;
  const sum = quotaSummary();
  const more = (sum.hot || 0) > 1 ? `+${sum.hot - 1} near limit` : null;
  const summary = ["Quotas", sum.label, more].filter(Boolean).join(" · ");
  const cls = sum.level === "wall" ? " wall" : sum.level === "high" ? " alarm" : "";
  return h("details", { class: `quota-strip${cls}`, "aria-label": "Agent quotas", "data-reading-key": "quotas" },
    h("summary", { class: "quota-summary", title: "From the Omarchy agent viewer" }, h("span", { text: summary }), icon("chev", "quota-chev")),
    h("div", { class: "quota-cards" }, arrangedAccounts(accounts).filter((a) => accountShown(a.id)).map(quotaCard)));
}

async function refreshQuotas() {
  app.quotaRefreshing=true;render(true);
  try { const result=await api(UI.api.quotas_refresh,{method:'POST',body:{}});flash(result.ok?'Quotas refreshed.':'Some quota collectors could not refresh.'); }
  catch(err) { flash(`Could not refresh quotas: ${err.message}`); }
  app.quotaRefreshing=false;await refresh(true);
}

// One quota-stop row; the overview, the graph sheet and the gauge details reuse it.
function quotaStopGroup(view, group) {
  return h("section",{class:"card quota-stop-group",id:`quota-stop-${group.account_id}`,"data-reading-key":`stop:${group.account_id}`},
    h("h3",{class:"sub",text:view.quota_stops.label}),h("p",{text:group.summary}),
    h("div",{class:"chips"},group.agents.map(a=>h("a",{class:"chip",href:hashLink("agent", a.identity),text:a.name}))),
    h("button",{class:"btn",type:"button",text:"Mark read",on:{click:async()=>{
      await api(UI.api.quota_stops_ack,{method:"POST",body:{identities:group.agents.map(a=>a.identity)}});await refresh(true);
    }}}));
}

function quotaStopBlock(view) {
  const groups=view.quota_stops?.groups || [];
  return h("div",{class:"quota-stop-groups","data-reading-key":"quota-stop-groups"},groups.map(group=>quotaStopGroup(view, group)));
}

// ---------------------------------------------------------------- quota gauges (v0.9.4)
// Labels: UI.quotas.labels (ui_contract.json). Gauge data: quotas.py (rings, level,
// flag, reset_label, agents_label, checked, healthy). Colours: --quota-* tokens.

function quotaText(key, values = {}) {
  return (UI.quotas.labels[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? ""));
}

// Account order has one rule: arrangedAccounts() / accountShown() from Arrange.
// Quotas only adds the fold: while Alex has saved no account order, pins or
// hidden accounts, the healthy accounts fold on one line ("4 accounts fine").
function quotaGroups(accounts) {
  const saved = ["account_order", "account_pinned", "account_hidden"].some((k) => (app.view?.layout?.[k] || []).length);
  const list = arrangedAccounts(accounts);
  const visible = list.filter((a) => accountShown(a.id));
  const fine = saved ? [] : visible.filter((a) => a.healthy);
  return { shown: visible.filter((a) => !fine.includes(a)), fine, hidden: list.filter((a) => !accountShown(a.id)) };
}

function svgNode(tag, attrs, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  for (const child of children.flat(Infinity)) if (child) node.appendChild(child);
  return node;
}

// ---------------------------------------------------------------- header mini dials (v0.9.7.4)
// Instead of the "Now: Cursor 100% ..." line: the first accounts in the Arrange order as small
// dials (quotas.header_dials; the server picks them, a nearly full account takes the last place).
// Tapping a dial opens that account's details on the Quotas page. Off: a plain "Quotas" link.
function quotaDialAccounts(view = app.view) {
  const quotas = view?.quotas || {};
  const byId = new Map((quotas.accounts || []).map((a) => [a.id, a]));
  return (quotas.dials || []).map((id) => byId.get(id)).filter(Boolean);
}

function openQuotaAccount(id) {
  app.quotaOpen = id;
  if (location.hash !== hashLink("quotas")) location.hash = hashLink("quotas");
  else render(true);
  requestAnimationFrame(() => document.getElementById("quota-open")?.scrollIntoView({ block: "nearest" }));
}

// v0.9.8.8: the number in the middle follows Quotas › Settings (fuller, weekly, 5-hour or both) and
// names its limit underneath (wk, 5h, mo). Older servers without account.centre: the fullest window.
function quotaCentre(account) {
  return account.centre || (account.rings?.length ? { text: `${account.worst_pct}%`, unit: "", title: "", both: false } : null);
}

function quotaCentreNodes(account, cls) {
  const c = quotaCentre(account);
  if (!c) return [h("span", { class: `${cls}-num`, text: "–" })];
  return [h("span", { class: `${cls}-num${c.both ? " is-both" : ""}`, text: c.text }),
    c.unit ? h("span", { class: `${cls}-unit`, text: c.unit }) : null];
}

function quotaSpoken(account) {
  const c = quotaCentre(account);
  return c ? `${c.text}${c.title ? ` ${c.title}` : ""}` : "";
}

// Model-only limits (Fable Weekly, Cursor's named models) are lines, not rings: they stop only the
// agents on that model. A gauge lists the ones from the warning level up; the details list all.
function quotaScopedLines(account, compact = false) {
  const warn = (account.levels || {}).warning ?? 70;
  const list = (account.scoped || []).filter((w) => compact ? w.pct >= 100 : w.pct >= warn)
    .sort((a, b) => b.pct - a.pct).slice(0, 1);   // the fullest one; the details list all
  return list.map((w) => h("span", { class: `quota-scoped q-${w.level}`, "data-reading-key": `scoped:${w.scope}`, title: w.label,
    text: compact ? `${w.scope} ${w.pct}%` : w.label }));
}

function quotaDial(account) {
  const all = account.rings || [];
  // Outer weekly, inner 5-hour; an account with only one window gets a single ring.
  const rings = all.length ? all.slice(0, 2) : [];
  const L = UI.quotas.labels;
  const spoken = (L.dial_open || "{name}").replace("{name}", account.name).replace("{pct}%", quotaSpoken(account) || "–").replace("{pct}", quotaSpoken(account) || "–");
  return h("button", { type: "button", class: `quota-dial-mini q-${account.level}${account.flag ? ` flagged f-${account.flag.id}` : ""}${account.stale ? " stale" : ""}`,
      "data-quota-dial": account.id, title: [spoken, account.flag?.label].filter(Boolean).join(" · "), "aria-label": [spoken, account.flag?.label].filter(Boolean).join(" "),
      on: { click: (e) => { e.preventDefault(); openQuotaAccount(account.id); } } },
    h("span", { class: "qd-ring" }, rings.length ? quotaRings(rings) : null),
    accountIcon(account.icon, { size: 12, text: false }),
    h("span", { class: "qd-pct" }, quotaCentreNodes(account, "qd")));
}

function renderQuotaDials(slot) {
  const accounts = quotaDialAccounts();
  const key = JSON.stringify(accounts.map((a) => [a.id, a.level, a.flag?.id, a.worst_pct, a.centre, a.stale, (a.rings || []).map((r) => [r.pct, r.level]), a.icon]));
  if (slot.dataset.key === key) return;
  slot.dataset.key = key;
  const hot = accounts.some((a) => a.level === "high" || a.level === "wall");
  slot.classList.toggle("caution", hot);
  if (!accounts.length) {
    slot.replaceChildren(h("a", { class: "chip quota-chip", href: hashLink("quotas"), text: "Quotas" }));
    return;
  }
  slot.replaceChildren(h("span", { class: "quota-dials", role: "group", "aria-label": UI.quotas.labels.dials || "Quotas" }, accounts.map(quotaDial)));
}

// Outer ring: weekly. Inner ring: 5-hour session (or the account's own windows).
// When one ring is switched off, the other takes the outer place.
function quotaRings(rings) {
  const radii = [44, 32];
  return svgNode("svg", { class: "quota-rings", viewBox: "0 0 100 100", "aria-hidden": "true" },
    rings.slice(0, 2).map((ring, i) => [
      svgNode("circle", { class: "ring-track", cx: 50, cy: 50, r: radii[i] }),
      ring.pct > 0 ? svgNode("circle", { class: `ring-fill q-${ring.level}`, cx: 50, cy: 50, r: radii[i], pathLength: 100,
        "stroke-dasharray": `${Math.min(100, ring.pct)} 100`, transform: "rotate(-90 50 50)" }) : null]));
}

function toggleQuota(id) {
  app.quotaOpen = app.quotaOpen === id ? null : id;
  render(true);
  if (app.quotaOpen) requestAnimationFrame(() => document.getElementById("quota-open")?.scrollIntoView({ block: "nearest" }));
}

// The flag and old data always show; the rings and numbers follow account.show.
// Compact gauges use the short reset time and "Old data" so each line fits.
function quotaGauge(account, compact = false) {
  const open = app.quotaOpen === account.id;
  const all = account.rings || [];
  const show = new Set(account.show || UI.quotas.parts.map((p) => p.id));
  const rings = all.filter((r) => show.has(r.kind || "weekly"));
  const spoken = [account.name, ...all.map((r) => `${r.title} ${r.pct}%`), ...(account.scoped || []).map((w) => w.label), account.reset_label, account.agents_label,
    account.flag?.label, account.stale ? account.checked : null].filter(Boolean).join(", ");
  return h("button", { type: "button", class: `quota-gauge q-${account.level}${account.flag ? " flagged" : ""}${account.stale ? " stale" : ""}`,
      "data-reading-key": `gauge:${account.id}`, "data-quota-id": account.id, "aria-expanded": String(open),
      "aria-label": spoken, title: quotaText("open"), on: { click: () => toggleQuota(account.id) } },
    rings.length || show.has("percent") ? h("span", { class: "quota-dial" }, rings.length ? quotaRings(rings) : null,
      show.has("percent") ? h("span", { class: "quota-center", title: quotaCentre(account)?.title || null }, quotaCentreNodes(account, "qc")) : null) : null,
    h("span", { class: "quota-gtitle" }, accountIcon(account.icon, { size: compact ? 13 : 15, text: false }),
      h("span", { class: "quota-gname", text: account.name })),
    account.flag ? h("span", { class: `quota-flag f-${account.flag.id}`, text: account.flag.label }) : null,
    ...quotaScopedLines(account, compact),
    show.has("reset") ? h("span", { class: "quota-gline", text: compact ? account.reset_short || account.reset_label : account.reset_label }) : null,
    show.has("agents") ? h("span", { class: "quota-gline", text: account.agents_label }) : null,
    account.stale ? h("span", { class: "quota-gline quota-old", text: compact ? quotaText("old_data") : account.checked }) : null);
}

// One grid, or one grid per provider (in the order of each provider's first
// account, so pins and Alex's order decide the groups and the order inside them).
function quotaGrid(list, grouped, compact) {
  const gauge = (a) => quotaGauge(a, compact);
  if (!grouped) return h("div", { class: "quota-gauges" }, list.map(gauge));
  const groups = new Map();
  for (const a of list) groups.set(a.group || a.provider, [...(groups.get(a.group || a.provider) || []), a]);
  return [...groups].map(([name, items]) => h("section", { class: "quota-group", "data-reading-key": `quota-group:${name}`, "aria-label": name },
    h("h2", { class: "sub quota-group-name", text: name }), h("div", { class: "quota-gauges" }, items.map(gauge))));
}

function quotaWindowRow(w) {
  const pct = quotaPct(w);
  return h("div", { class: "quota-window" },
    h("span", { class: "quota-wname", text: w.title || w.label }),
    h("div", { class: `quota-meter q-${w.level || "low"}`, role: "img", "aria-label": `${w.title || w.label} ${pct} percent used` }, h("i", { style: { width: `${pct}%` } })),
    h("span", { class: "quota-pct", text: `${pct}%` }),
    w.resets_label ? h("span", { class: "quota-line", text: quotaText("resets", { time: w.resets_label }) }) : null);
}

// Details for one gauge: limits, age, agents on it, agents stopped by it (with the
// existing quota-stop row), and daily messages when the record has them.
function quotaDetail(view, account) {
  const pins = view.settings["quotas.header_accounts"] || [];
  const running = (account.agents || []).filter((a) => a.present && !a.stopped);
  const groups = (view.quota_stops?.groups || []).filter((g) => g.account_id === account.id);
  const inRows = new Set(groups.flatMap((g) => g.agents.map((a) => a.identity)));
  const stopped = (account.agents || []).filter((a) => a.stopped && !inRows.has(a.identity));
  const history = account.history || [];
  const most = Math.max(1, ...history.map((d) => d.messages));
  const known = new Map((view.agents || []).map((x) => [x.identity, x]));
  const chip = (a) => h("a", { class: "chip", href: hashLink("agent", a.identity) }, a.name, modelBadge(known.get(a.identity)));
  return h("section", { id: "quota-open", class: "card quota-open", "data-reading-key": `quota-open:${account.id}`, "aria-label": account.name },
    h("div", { class: "quota-open-head" }, h("h2", { class: "sub quota-open-title" }, accountIcon(account.icon, { size: 28, text: false }), account.name),
      h("button", { class: "btn quiet", type: "button", text: quotaText("close"), on: { click: () => toggleQuota(account.id) } })),
    h("p", { class: "muted small", text: [account.provider_name, account.plan, account.flag?.label, account.status].filter(Boolean).join(" · ") }),
    h("h3", { class: "sub", text: quotaText("limits") }),
    (account.windows || []).length ? (account.windows || []).map(quotaWindowRow) : h("p", { class: "muted small", text: account.note || quotaText("no_limits") }),
    (account.scoped || []).length ? h("p", { class: "muted small quota-scoped-note",
      text: quotaText("scoped_note", { titles: account.scoped.map((w) => w.title).join(", ") }) }) : null,
    h("p", { class: "quota-reset", text: account.reset_text }),
    h("p", { class: `small ${account.stale ? "quota-old" : "muted"}`, text: account.stale ? `${quotaText("old_data")} · ${account.checked}` : account.checked }),
    h("h3", { class: "sub", text: quotaText("on_account") }),
    running.length ? h("div", { class: "chips" }, running.map(chip)) : h("p", { class: "muted small", text: quotaText("agents_none") }),
    stopped.length || groups.length ? [h("h3", { class: "sub", text: quotaText("stopped") }),
      stopped.length ? h("div", { class: "chips" }, stopped.map(chip)) : null, groups.map((g) => quotaStopGroup(view, g))] : null,
    history.length ? [h("h3", { class: "sub", text: quotaText("history") }),
      h("div", { class: "quota-history", role: "img", "aria-label": history.map((d) => `${d.date}: ${d.messages}`).join(", ") },
        history.map((d) => h("span", { title: `${d.date}: ${d.messages}`, style: { "--h": `${Math.round((d.messages / most) * 100)}%` } })))] : null,
    h("div", { class: "setting" }, h("span", { class: "label", text: quotaText("pin") }),
      toggle(pins.includes(account.id), `${quotaText("pin")}: ${account.name}`,
        (on) => saveSetting("quotas.header_accounts", on ? [...pins, account.id] : pins.filter((id) => id !== account.id)))),
    h("div", { class: "actions" }, h("button", { class: "btn quiet", type: "button", text: quotaText("settings_account"),
      on: { click: () => openQuotaSettings(account.id) } })));
}

// ---------------------------------------------------------------- quota view settings (v0.9.5)
// On the Quotas page, behind one button at the top. Saved on the server like
// Arrange (config quotas.* and layout.account_*), so every screen agrees.
// Changes wait 250 ms and then save in one go; the last change wins.

function quotaSetting(key) {
  const pending = app.quotaSettings?.pending || {};
  return Object.hasOwn(pending, key) ? pending[key] : app.view?.settings?.[key];
}

function openQuotaSettings(scope = null) {
  const qs = app.quotaSettings || { scope: "all", status: "", pending: {}, error: "" };
  if (scope) qs.scope = scope;
  app.quotaSettings = qs;
  render(true);
  requestAnimationFrame(() => document.getElementById("quota-settings")?.scrollIntoView({ block: "nearest" }));
}

function closeQuotaSettings() {
  app.quotaSettings = null;
  render(true);
  document.querySelector(".quota-settings-open")?.focus();
}

function quotaSave(changes, focus = null) {
  const qs = app.quotaSettings;
  if (!qs) return;
  Object.assign(qs.pending, changes);
  qs.status = quotaText("saving");
  qs.focus = focus;
  quotaRender();
  clearTimeout(quotaSave.timer);
  quotaSave.timer = setTimeout(() => {
    quotaSave.timer = null;
    const body = { ...qs.pending };
    app.quotaQueue = (app.quotaQueue || Promise.resolve()).then(async () => {
      const ok = await saveSettings(body);
      if (app.quotaSettings !== qs) return;
      for (const [key, value] of Object.entries(body)) if (qs.pending[key] === value) delete qs.pending[key];
      if (!ok) qs.pending = {};   // show what is really saved
      if (!quotaSave.timer) qs.status = ok ? quotaText("saved") : "";
      quotaRender();
    });
  }, 250);
}

// Re-render and put the keyboard focus back on the control that was used.
function quotaRender() {
  render(true);
  const key = app.quotaSettings?.focus;
  if (!key) return;
  app.quotaSettings.focus = null;
  const target = [...document.querySelectorAll("[data-quota-focus]")].find((el) => el.dataset.quotaFocus === key);
  if (target && document.activeElement !== target) (target.disabled ? target.closest("li")?.querySelector("button:not(:disabled)") : target)?.focus();
}

// The saved order with changes not yet saved on top; the same rule as Arrange.
function quotaLayout() {
  const layout = arrangeLayout();
  for (const key of ["account_order", "account_pinned", "account_hidden"]) {
    const value = quotaSetting(`layout.${key}`);
    if (Array.isArray(value)) layout[key] = [...value];
  }
  return layout;
}

function quotaMove(id, delta) {
  const layout = quotaLayout(), list = arrangedAccounts(app.view?.quotas?.accounts || [], layout).map((a) => a.id);
  const index = list.indexOf(id), target = list[index + delta];
  if (!target || layout.account_pinned.includes(id) !== layout.account_pinned.includes(target)) return;   // pinned stay first
  list.splice(index, 1);
  list.splice(index + delta, 0, id);
  quotaSave({ "layout.account_order": mergeOrder(list, layout.account_order) }, `${id}:${delta < 0 ? "up" : "down"}`);
}

// Pin goes to the end of the pinned group, unpin to the top of the rest (as in Arrange).
function quotaToggleList(key, id) {
  const layout = quotaLayout();
  const order = mergeOrder(arrangedAccounts(app.view?.quotas?.accounts || [], layout).map((a) => a.id), layout.account_order);
  const list = layout[key].includes(id) ? layout[key].filter((x) => x !== id) : [...layout[key], id];
  quotaSave({ "layout.account_order": order, [`layout.${key}`]: list }, `${id}:${key}`);
}

function quotaArrangeRows(accounts) {
  const layout = quotaLayout(), L = UI.arrange.labels;
  const list = arrangedAccounts(accounts, layout), pinned = (a) => !!a && layout.account_pinned.includes(a.id);
  const button = (a, cls, text, label, focus, onClick, disabled = false) => h("button", { class: `btn quiet ${cls}`, type: "button", text,
    "aria-label": `${label}: ${quotaName(a)}`, disabled, "data-quota-focus": `${a.id}:${focus}`, on: { click: onClick } });
  return h("ol", { class: "arrange-list quota-arrange", "data-reading-key": "quota-arrange" }, list.map((a, i) => {
    const hidden = layout.account_hidden.includes(a.id), pin = pinned(a);
    const tags = [pin ? L.pinned : null, hidden ? L.hidden : null, a.flag?.label].filter(Boolean).join(" · ");
    const first = i === 0 || pin !== pinned(list[i - 1]), last = i === list.length - 1 || pin !== pinned(list[i + 1]);
    return h("li", { class: `arrange-row${hidden ? " is-hidden" : ""}${pin ? " is-pinned" : ""}`, "data-reading-key": `qs-account:${a.id}`, "data-account-id": a.id },
      accountIcon(a.icon, { size: 22, text: false }),
      h("span", { class: "arrange-name" }, h("span", { text: quotaName(a) }), tags ? h("span", { class: "muted small", text: tags }) : null),
      button(a, "arrange-pin", pin ? L.unpin : L.pin, pin ? L.unpin : L.pin, "account_pinned", () => quotaToggleList("account_pinned", a.id)),
      button(a, "arrange-hide", hidden ? L.show : L.hide, hidden ? L.show : L.hide, "account_hidden", () => quotaToggleList("account_hidden", a.id)),
      button(a, "arrange-move", "↑", L.up, "up", () => quotaMove(a.id, -1), first),
      button(a, "arrange-move", "↓", L.down, "down", () => quotaMove(a.id, 1), last));
  }));
}

// Gauge parts and warning levels: for all accounts, or own ones for one account.
function quotaScopeSettings(account) {
  const parts = UI.quotas.parts.map((p) => p.id);
  const allShow = quotaSetting("quotas.gauge_show") || parts, perShow = quotaSetting("quotas.gauge_accounts") || {};
  const allLevels = quotaSetting("quotas.levels") || UI.quotas.levels, perLevels = quotaSetting("quotas.account_levels") || {};
  const ownShow = !!account && Object.hasOwn(perShow, account.id), ownLevels = !!account && Object.hasOwn(perLevels, account.id);
  const show = ownShow ? perShow[account.id] : allShow, levels = ownLevels ? perLevels[account.id] : allLevels;
  const scope = account ? account.id : "all";
  const without = (map, id) => Object.fromEntries(Object.entries(map).filter(([key]) => key !== id));
  const saveShow = (next) => quotaSave(account ? { "quotas.gauge_accounts": { ...perShow, [account.id]: next } } : { "quotas.gauge_show": next });
  const saveLevels = (form) => {
    const warning = Number(form.querySelector("[name=warning]").value), critical = Number(form.querySelector("[name=critical]").value);
    const whole = (n) => Number.isInteger(n) && n >= 1 && n <= 100;
    app.quotaSettings.error = whole(warning) && whole(critical) && warning < critical ? "" : quotaText("settings_levels_order");
    if (app.quotaSettings.error) { quotaRender(); return; }
    const next = { warning, critical };
    quotaSave(account ? { "quotas.account_levels": { ...perLevels, [account.id]: next } } : { "quotas.levels": next });
  };
  const same = (own, key, map) => own ? h("button", { class: "btn quiet", type: "button", text: quotaText("settings_same"),
    "aria-label": `${quotaText("settings_same")}: ${account.name}`, on: { click: () => quotaSave({ [key]: without(map, account.id) }) } }) : null;
  const number = (name, label, value) => h("label", { class: "quota-level" }, h("span", { text: label }),
    h("input", { type: "number", name, min: 1, max: 100, step: 1, inputmode: "numeric", value, "aria-label": `${label} (%)`,
      on: { change: (e) => saveLevels(e.target.closest("fieldset")) } }), h("span", { class: "muted", text: "%" }));
  return [
    h("fieldset", { class: "quota-parts", "data-reading-key": `qs-show:${scope}:${ownShow}` },
      h("legend", { text: quotaText("settings_show") + (ownShow ? ` · ${quotaText("settings_own")}` : "") }),
      UI.quotas.parts.map((p) => {
        const input = h("input", { type: "checkbox", value: p.id,
          on: { change: (e) => saveShow(parts.filter((id) => (id === p.id ? e.target.checked : show.includes(id)))) } });
        input.checked = show.includes(p.id);
        return h("label", { class: "quota-part" }, input, h("span", { text: p.label }));
      }),
      h("p", { class: "muted small", text: quotaText("settings_rings_note") }),
      same(ownShow, "quotas.gauge_accounts", perShow)),
    h("fieldset", { class: "quota-levels", "data-reading-key": `qs-levels:${scope}:${levels.warning}-${levels.critical}` },
      h("legend", { text: quotaText("settings_levels") + (ownLevels ? ` · ${quotaText("settings_own")}` : "") }),
      h("div", { class: "quota-level-row" }, number("warning", quotaText("settings_warning"), levels.warning),
        number("critical", quotaText("settings_critical"), levels.critical)),
      app.quotaSettings.error ? h("p", { class: "quota-old small", role: "alert", text: app.quotaSettings.error }) : null,
      h("p", { class: "muted small", text: quotaText("settings_levels_note") }),
      same(ownLevels, "quotas.account_levels", perLevels)),
  ];
}

function quotaSettingsPanel(accounts) {
  const qs = app.quotaSettings, L = UI.quotas.labels;
  const size = quotaSetting("quotas.gauge_size") || UI.quotas.sizes[0].id, grouped = !!quotaSetting("quotas.group_by_provider");
  const dials = quotaSetting("quotas.header_dials") ?? app.view?.quotas?.view?.header_dials ?? true;
  const dialCount = Number(quotaSetting("quotas.header_dials_count") ?? app.view?.quotas?.view?.header_dials_count ?? 3);
  const centre = quotaSetting("quotas.centre") ?? app.view?.quotas?.view?.centre ?? "higher";
  const account = accounts.find((a) => a.id === qs.scope) || null;
  if (!account) qs.scope = "all";
  const scope = h("select", { "aria-label": L.settings_for, "data-reading-key": `qs-scope:${qs.scope}`,
      on: { change: (e) => { qs.scope = e.target.value; qs.error = ""; render(true); } } },
    h("option", { value: "all", text: L.settings_all }),
    arrangedAccounts(accounts, quotaLayout()).map((a) => h("option", { value: a.id, text: quotaName(a) })));
  scope.value = qs.scope;
  return h("section", { id: "quota-settings", class: "card quota-settings", "data-reading-key": "quota-settings", "aria-labelledby": "quota-settings-title" },
    h("div", { class: "quota-open-head" }, h("h2", { id: "quota-settings-title", class: "sub", text: L.settings_title }),
      h("button", { class: "btn", type: "button", text: L.settings_done, on: { click: closeQuotaSettings } })),
    h("p", { class: "muted small", text: L.settings_intro }),
    h("h3", { class: "sub", text: L.settings_accounts }),
    accounts.length ? quotaArrangeRows(accounts) : h("p", { class: "muted", text: UI.arrange.labels.no_accounts }),
    h("div", { class: "setting" }, h("span", { class: "label", text: L.settings_size }),
      h("div", { class: "segmented", role: "group", "aria-label": L.settings_size },
        UI.quotas.sizes.map((s) => h("button", { type: "button", "aria-pressed": String(size === s.id), text: s.label,
          "data-quota-focus": `size:${s.id}`, on: { click: () => quotaSave({ "quotas.gauge_size": s.id }, `size:${s.id}`) } })))),
    h("div", { class: "setting" }, h("span", { class: "label" }, L.settings_group, h("small", { text: L.settings_group_note })),
      toggle(grouped, L.settings_group, (on) => quotaSave({ "quotas.group_by_provider": on }))),
    h("div", { class: "setting", "data-reading-key": "qs-dials" }, h("span", { class: "label" }, L.settings_dials, h("small", { text: L.settings_dials_note })),
      toggle(dials, L.settings_dials, (on) => quotaSave({ "quotas.header_dials": on }))),
    dials ? h("div", { class: "setting" }, h("span", { class: "label", text: L.settings_dials_count }),
      h("div", { class: "segmented", role: "group", "aria-label": L.settings_dials_count },
        [1, 2, 3, 4, 5, 6].map((n) => h("button", { type: "button", "aria-pressed": String(dialCount === n), text: String(n),
          "data-quota-focus": `dials:${n}`, on: { click: () => quotaSave({ "quotas.header_dials_count": n }, `dials:${n}`) } })))) : null,
    h("div", { class: "setting stack", "data-reading-key": "qs-centre" }, h("span", { class: "label" }, L.settings_centre, h("small", { text: L.settings_centre_note })),
      h("div", { class: "segmented", role: "group", "aria-label": L.settings_centre },
        UI.quotas.centres.map((c) => h("button", { type: "button", "aria-pressed": String(centre === c.id), text: c.label,
          "data-quota-focus": `centre:${c.id}`, on: { click: () => quotaSave({ "quotas.centre": c.id }, `centre:${c.id}`) } })))),
    h("label", { class: "setting quota-scope" }, h("span", { class: "label", text: L.settings_for }), scope),
    quotaScopeSettings(account),
    h("details", { class: "quota-icons", "data-reading-key": "qs-icons", open: app.iconOpen ? "" : null },
      h("summary", {}, h("span", { text: UI.account_icons.labels.open_from_quotas }), icon("chev", "quota-chev")), accountIconsCard("quotas")),
    h("p", { class: "muted small quota-settings-status", role: "status", text: qs.status || "" }));
}

function renderQuotas() {
  const view = app.view, quotas = view.quotas || {}, accounts = quotas.accounts || [];
  const { shown, fine, hidden } = quotaGroups(accounts);
  if (app.quotaOpen && !accounts.some((a) => a.id === app.quotaOpen)) app.quotaOpen = null;
  const open = accounts.find((a) => a.id === app.quotaOpen);
  // Size and grouping show at once; the server value once saved.
  const pending = app.quotaSettings?.pending || {};
  const size = pending["quotas.gauge_size"] ?? quotas.view?.size ?? UI.quotas.sizes[0].id;
  const grouped = pending["quotas.group_by_provider"] ?? !!quotas.view?.group_by_provider;
  const compact = size === "compact";
  const fold = (key, label, list) => list.length ? h("details", { class: "quota-fold", "data-reading-key": `quota-fold:${key}` },
    h("summary", { text: label }), h("div", { class: "quota-gauges" }, list.map((a) => quotaGauge(a, compact)))) : null;
  const missing = quotas.unassigned || [];
  return h("div", { "data-reading-key": "quota-view", class: `quota-view q-size-${size}` },
    h("div", { class: "quota-head" }, h("h1", { text: "Quotas" }),
      h("button", { class: "btn quota-settings-open", type: "button", "aria-expanded": String(!!app.quotaSettings), "aria-controls": "quota-settings",
        on: { click: () => (app.quotaSettings ? closeQuotaSettings() : openQuotaSettings()) } }, icon("gear", "quota-gear"), quotaText("settings_open"))),
    app.quotaSettings ? quotaSettingsPanel(accounts) : null,
    accounts.length ? h("div", { class: "quota-main", "data-reading-key": `quota-gauges:${grouped ? "grouped" : "one"}` }, quotaGrid(shown, grouped, compact))
      : h("p", { text: quotas.error || quotaText("no_records") }),
    fold("fine", fine.length === 1 ? quotaText("fine_one") : quotaText("fine", { n: fine.length }), fine),
    fold("hidden", quotaText("hidden", { n: hidden.length }), hidden),
    open ? quotaDetail(view, open) : null,
    // Each running agent once (no subagents, no ended ones); nothing when all are known.
    quotas.unassigned_agents ? h("details", { class: "note quota-unassigned", "data-reading-key": "quota-unassigned" },
      h("summary", {}, h("span", { text: quotas.unassigned_agents === 1 ? quotaText("unassigned_one") : quotaText("unassigned", { n: quotas.unassigned_agents }) }),
        icon("chev", "quota-chev")),
      h("div", { class: "chips" }, missing.map((a) => h("a", { class: "chip", href: hashLink("agent", a.identity), text: a.name })))) : null,
    h("div", { class: "actions" }, h("button", { class: "btn", type: "button", disabled: app.quotaRefreshing, on: { click: refreshQuotas },
      text: app.quotaRefreshing ? quotaText("refreshing") : quotaText("refresh") })),
    h("p", { class: "muted small", text: "Refresh runs the existing usage collectors only when pressed. The Claude probe may use a small Haiku call. Accounts are never switched." }),
    gateHealthLine());
}

// ---------------------------------------------------------------- account icons (v0.9.7)
// The one renderer: every view calls accountIcon(icon, {size}); no view draws an account icon
// itself. The icon object comes from the server (account_icons.py); the marks and colour names
// from UI.account_icons (ui_contract.json); colours are the live theme tokens --icon-<name>.
// No icon object means the account is not recorded: nothing is drawn (never guessed).
const ACCOUNT_GLYPHS = Object.fromEntries(UI.account_icons.glyphs.map((g) => [g.id, g]));
const ACCOUNT_COLORS = new Set(UI.account_icons.colors.map((c) => c.id));

// Returns an image, a mark with its colour and badge, or the text label (no mark and no image).
// {size} is the side in px; {text: false} returns null instead of the text label, for places
// that already show the account's name next to it.
// A stroke mark with its own box keeps at least our marks' weight (2 per 24 units), so a fine
// line (Omarchy's Cursor cube, 4 on 64) stays visible at 12-16 px.
function markStroke(glyph) {
  const side = Math.max(...(glyph.view_box || "0 0 24 24").split(" ").slice(2).map(Number));
  return Math.max(glyph.stroke_width || 2.2, (2 * side) / 24);
}

function accountIcon(icon, { size = 22, text = true } = {}) {
  if (!icon) return null;
  const color = ACCOUNT_COLORS.has(icon.color) ? icon.color : "neutral";
  const glyph = ACCOUNT_GLYPHS[icon.glyph];
  const shared = { "data-account": icon.account, title: icon.label, style: { "--acct-size": `${size}px` } };
  const badge = icon.badge ? h("b", { class: "acct-badge", "aria-hidden": "true", text: icon.badge }) : null;
  if (icon.image) {
    // A missing or broken image falls back to the mark (or the label) in place.
    const fallback = (e) => { const node = e.currentTarget.closest(".acct-icon");
      const next = accountIcon({ ...icon, image: null }, { size, text: true }); if (node && next) node.replaceWith(next); };
    return h("span", { ...shared, class: `acct-icon acct-image acct-${color}`, role: "img", "aria-label": icon.label },
      h("img", { src: icon.image, alt: "", width: size, height: size, decoding: "async", on: { error: fallback } }), badge);
  }
  if (glyph) {
    // v0.9.8.5: a mark is a stroke path on a 24 x 24 box (our own shapes) or a filled mark from
    // Omarchy's agent icons with its own viewBox (third_party/omarchy-agents); both in the colour.
    const filled = glyph.paint === "fill";
    const path = filled ? svgNode("path", { d: glyph.d, "fill-rule": glyph.fill_rule || "nonzero" })
      : svgNode("path", { d: glyph.d });
    // Set through the CSSOM: the page's CSP allows no inline style attributes.
    if (!filled && glyph.stroke_width) path.style.strokeWidth = String(markStroke(glyph));
    return h("span", { ...shared, class: `acct-icon acct-${color}${size <= 16 ? " is-small" : ""}`, role: "img", "aria-label": icon.label, "data-glyph": glyph.id },
      svgNode("svg", { class: `acct-glyph${filled ? " is-fill" : ""}`, viewBox: glyph.view_box || "0 0 24 24", "aria-hidden": "true" }, path), badge);
  }
  return text ? h("span", { ...shared, class: `acct-icon acct-text acct-${color}`, text: icon.label }) : null;
}

// ---------------------------------------------------------------- account icon settings (v0.9.7)
// One editor, in Settings ("Account icons") and in the Quotas page panel. The choices are the
// setting accounts.icons, saved like Arrange (the whole map, 250 ms after the last change, the
// last change wins). An image is made small in the browser, then sent to
// POST /api/account-icons/<account>; Remove image and Reset use DELETE.

function iconChoices() {
  return app.iconDraft ?? app.view?.settings?.["accounts.icons"] ?? {};
}

function accountIconPath(id) {
  return UI.api.account_icon.replace("{id}", encodeURIComponent(id));
}

function iconSave(id, entry) {
  const next = { ...iconChoices() };
  if (entry && Object.keys(entry).length) next[id] = entry; else delete next[id];
  app.iconDraft = next;
  app.iconStatus = { account: id, text: UI.account_icons.labels.saving };
  render(true);
  clearTimeout(iconSave.timer);
  iconSave.timer = setTimeout(() => {
    iconSave.timer = null;
    const body = app.iconDraft;
    app.iconQueue = (app.iconQueue || Promise.resolve()).then(async () => {
      const ok = await saveSettings({ "accounts.icons": body });
      if (!ok || (app.iconDraft === body && !iconSave.timer)) app.iconDraft = null;   // show what is really saved
      if (!iconSave.timer) app.iconStatus = { account: id, text: ok ? UI.account_icons.labels.saved : "" };
      render(true);
    });
  }, 250);
}

// The server's icon with choices not yet saved on top, so the preview follows every tap.
function iconPreview(account) {
  const icon = account.icon || { account: account.id, label: quotaName(account), glyph: null, color: "neutral", badge: null, image: null, custom: false };
  const entry = app.iconDraft?.[account.id];
  if (!entry) return icon;
  const pick = (key) => (key in entry ? entry[key] : icon[key]);
  return { ...icon, glyph: pick("glyph"), color: pick("color"), badge: pick("badge"), image: entry.image === false ? null : icon.image, custom: true };
}

async function shrinkAccountImage(file) {
  const L = UI.account_icons.labels;
  if (!file || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error(L.not_image);
  let bitmap;
  try { bitmap = await createImageBitmap(file); } catch { throw new Error(L.not_image); }
  // A centred square, at most image_px wide: small enough for every place an icon shows.
  const side = Math.min(bitmap.width, bitmap.height), out = Math.max(1, Math.min(UI.account_icons.image_px, side));
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = out;
  canvas.getContext("2d").drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, out, out);
  for (const type of ["image/webp", "image/png", "image/jpeg"]) {
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, type, 0.86));
    if (blob && blob.type === type && blob.size <= UI.account_icons.image_max_bytes) return blob;
  }
  throw new Error(L.too_big);
}

async function iconRequest(id, work, busy) {
  const L = UI.account_icons.labels;
  app.iconStatus = { account: id, text: busy };
  render(true);
  let text = L.saved;
  try { await work(); await refresh(true); } catch (err) { text = L.upload_failed.replace("{error}", err.message); }
  app.iconStatus = { account: id, text };
  render(true);
}

function uploadAccountImage(id, file) {
  return iconRequest(id, async () => api(accountIconPath(id), { method: "POST", body: await shrinkAccountImage(file) }),
    UI.account_icons.labels.uploading);
}

function removeAccountImage(id) {
  return iconRequest(id, () => api(accountIconPath(id), { method: "DELETE" }), UI.account_icons.labels.saving);
}

async function resetAccountIcon(id, entry) {
  if (entry.image) await removeAccountImage(id);
  iconSave(id, null);
}

function accountIconEditor(account, icon, entry, status) {
  const L = UI.account_icons.labels, id = account.id, name = quotaName(account);
  // A mark or text only replaces an uploaded image (the file stays until Remove image or Reset).
  const set = (change) => iconSave(id, { ...entry, ...change });
  const group = (label, ...children) => h("div", { class: "acct-field" }, h("span", { class: "label", text: label }),
    h("div", { class: "acct-choices", role: "group", "aria-label": `${label}: ${name}` }, children));
  const file = h("input", { type: "file", class: "acct-file", accept: "image/png,image/jpeg,image/webp", "aria-label": `${L.upload}: ${name}`,
    on: { change: (e) => uploadAccountImage(id, e.target.files?.[0]) } });
  const badge = h("input", { type: "text", class: "acct-badge-input", maxlength: String(UI.account_icons.badge_max), value: icon.badge || "",
    autocapitalize: "characters", autocomplete: "off", spellcheck: "false", "aria-label": `${L.badge}: ${name}`,
    on: { change: (e) => set({ badge: e.target.value.trim() || null }) } });
  return h("div", { class: "acct-editor", "data-reading-key": `acct-editor:${id}` },
    group(L.mark, UI.account_icons.glyphs.map((g) => h("button", { type: "button", class: "acct-choice", title: g.label, "aria-label": g.label,
        "aria-pressed": String(!icon.image && icon.glyph === g.id), on: { click: () => set({ glyph: g.id, ...(entry.image ? { image: false } : {}) }) } },
        accountIcon({ ...icon, glyph: g.id, image: null, badge: null }, { size: 26 }))),
      h("button", { type: "button", class: "acct-choice acct-choice-text", text: L.text_only, "aria-pressed": String(!icon.image && !icon.glyph),
        on: { click: () => set({ glyph: null, ...(entry.image ? { image: false } : {}) }) } })),
    group(L.color, UI.account_icons.colors.map((c) => h("button", { type: "button", class: `acct-swatch acct-${c.id}`, title: c.label,
      "aria-label": c.label, "aria-pressed": String(icon.color === c.id), on: { click: () => set({ color: c.id }) } }))),
    h("label", { class: "acct-field" }, h("span", { class: "label", text: L.badge }),
      h("span", { class: "acct-badge-row" }, badge, h("small", { class: "muted", text: L.badge_hint }))),
    h("div", { class: "acct-field" }, h("span", { class: "label", text: L.image }),
      h("div", { class: "acct-choices" }, h("label", { class: "btn acct-upload" }, icon.image ? L.replace : L.upload, file),
        icon.image ? h("button", { type: "button", class: "btn quiet", text: L.remove_image, on: { click: () => removeAccountImage(id) } }) : null),
      h("small", { class: "muted", text: L.image_hint })),
    h("div", { class: "actions acct-actions" },
      Object.keys(entry).length ? h("button", { type: "button", class: "btn quiet", text: L.reset, "aria-label": `${L.reset}: ${name}`,
        on: { click: () => resetAccountIcon(id, entry) } }) : null,
      h("span", { class: "muted small", role: "status", text: status })));
}

// Every account in Alex's order: its icon, Default or Changed, and Change to open the editor.
function accountIconsCard(where) {
  const L = UI.account_icons.labels, accounts = arrangedAccounts(app.view?.quotas?.accounts || []);
  const choices = iconChoices();
  return h("div", { class: "card acct-settings", "data-reading-key": `acct-settings:${where}` },
    h("p", { class: "muted small", text: L.intro }),
    accounts.length ? h("ul", { class: "plain acct-list" }, accounts.map((account) => {
      const icon = iconPreview(account), open = app.iconOpen === account.id, entry = choices[account.id] || {};
      const status = app.iconStatus?.account === account.id ? app.iconStatus.text : "";
      return h("li", { class: `acct-row${open ? " is-open" : ""}`, "data-reading-key": `acct:${account.id}`, "data-account-id": account.id },
        h("div", { class: "acct-row-head" }, accountIcon(icon, { size: 32 }),
          h("span", { class: "acct-row-name" }, h("span", { text: quotaName(account) }),
            h("small", { class: "muted", text: [Object.keys(entry).length ? L.changed : L.default, open ? null : status].filter(Boolean).join(" · ") })),
          h("button", { type: "button", class: "btn quiet", "aria-expanded": String(open), text: open ? L.done : L.edit,
            "aria-label": `${open ? L.done : L.edit}: ${quotaName(account)}`, on: { click: () => { app.iconOpen = open ? null : account.id; render(true); } } })),
        open ? accountIconEditor(account, icon, entry, status) : null);
    })) : h("p", { class: "muted", text: L.no_accounts }));
}

// ---------------------------------------------------------------- overview

function stateBar(counts) {
  const total = STATES.reduce((sum, s) => sum + (counts[s] || 0), 0);
  const bar = h("div", { class: "bar", role: "img", "aria-label": legendText(counts) || "no agents" });
  for (const s of STATES) {
    if (counts[s]) bar.appendChild(h("span", { class: `s-${s}`, style: { flex: String(counts[s] / Math.max(1, total)) } }));
  }
  return bar;
}

function legendText(counts) {
  return STATES.filter((s) => counts[s]).map((s) => `${counts[s]} ${STATE_SHORT[s]}`).join(", ");
}

function legend(counts) {
  return h("div", { class: "legend" },
    STATES.filter((s) => counts[s]).map((s) =>
      h("span", { class: `s-${s}` }, h("i", { class: "dot state" }), h("b", { text: counts[s] }), STATE_SHORT[s])));
}

function workspaceLiveCount(ws) {
  return ws.live_agent_count ?? ws.agent_count ?? 0;
}

function activeWorkspaces(view) {
  return view.workspaces.filter((ws) => workspaceLiveCount(ws) > 0);
}

function emptyWorkspaceGroup(view) {
  const empty = view.workspaces.filter((ws) => workspaceLiveCount(ws) === 0);
  if (!empty.length) return null;
  return h("details", { class: "empty-workspaces muted", "data-reading-key": "empty-workspaces" },
    h("summary", { text: `${empty.length} workspace${empty.length === 1 ? "" : "s"} without agents` }),
    h("ul", {}, empty.map((ws) => h("li", {},
      h("a", { href: hashLink('workspace', ws.id), text: ws.label }), " · 0 live agents"))));
}

function tidyTips(n) {
  return `${n} tidy-up tip${n === 1 ? "" : "s"}`;
}

function layoutHints(hints) {
  if (!hints?.length) return null;
  return h("details", { class: "note layout-hints", "data-reading-key": "layout-hints" },
    h("summary", { text: tidyTips(hints.length) }),
    h("ul", {}, hints.map((hint) => h("li", {},
      h("span", { text: hint.message }),
      (hint.commands || []).map((command) => h("div", { class: "small" },
        h("code", { style: { overflowWrap: "anywhere", whiteSpace: "normal" }, text: command })))))));
}

// Status report buttons sit outside the card link (a near miss must not start an
// AI report) and name their coordinator when there is more than one.
function workspaceCard(ws) {
  const coordinators = (ws.coordinators || []).map(id => app.view.agents.find(a=>a.identity===id)).filter(Boolean);
  const reports = coordinators.map(a => statusReportButton(a, { named: coordinators.length > 1 })).filter(Boolean);
  const card = workspaceLink(ws);
  return reports.length ? h("div", { class: "ws-item" }, card, h("div", { class: "actions ws-reports" }, reports)) : card;
}

function workspaceLink(ws) {
  const latest = ws.last_activity_at ? `active ${ago(ws.last_activity_at)} ago` : "no activity seen yet";
  return h("a", { class: "card ws", href: hashLink('workspace', ws.id) },
    h("div", { class: "ws-head" },
      h("div", { class: "ws-name" }, ws.label, h("div", { class: "ws-sub", text: `${ws.agent_count} agent${ws.agent_count === 1 ? "" : "s"} · ${ws.tabs.length} tab${ws.tabs.length === 1 ? "" : "s"}` })),
      ws.attention ? h("span", { class: ws.counts.waiting_on_you ? "badge urgent" : "badge", title: ws.counts.waiting_on_you ? "waiting on you" : "to look at", text: ws.counts.waiting_on_you || ws.attention }) : null),
    ws.layout_hints?.length ? h("p", { class: "muted small", text: tidyTips(ws.layout_hints.length) }) : null,
    ws.agent_count ? stateBar(ws.counts) : h("p", { class: "muted small", text: "No agents running here right now." }),
    ws.agent_count ? legend(ws.counts) : null,
    h("div", { class: "ws-foot" },
      h("span", { text: latest }),
      ws.muted ? h("span", { text: "phone alerts muted" }) : null,
      ws.stalled ? h("span", { class: "warn", text: `${ws.stalled} possibly stalled` }) : null));
}

// One line for lists: the AI headline when it is about the current item,
// otherwise the plain label and task (never a made-up summary).
function summaryLine(a) {
  const s = a.summary;
  if (s && s.status === "complete" && s.current && s.headline)
    return h("span", {}, h("span", { class: "ai-tag", title: "AI summary", text: "AI" }), " ", mdLine(s.headline));
  const busy = s && s.current && (s.status === "queued" || s.status === "running") ? " · summarizing…" : "";
  return `${a.label}${a.title ? `: ${a.title}` : ""}${busy}`;
}

// Tier ids, order and headings come from the server's single wording table
// (view.notify_tiers.tiers, notify_tiers.py). No copies here.
function tierTable() {
  return (app.view && app.view.notify_tiers && app.view.notify_tiers.tiers) || [];
}

function tierOrder() {
  return tierTable().map((t) => t.id);
}

function tierHead(id) {
  const tier = tierTable().find((t) => t.id === id);
  return tier ? tier.label : id;
}

function agentTier(a) {
  if ("tier" in a) return a.tier;
  if (a.acked) return null;
  if (NEEDS.includes(a.kind)) return "waiting_on_you";
  if (a.kind === "completion") return "fyi_finished";
  if (a.kind === "stall") return "might_look";
  return null;
}

function draftQuestionReply(question, text=null) {
  openReply(question.identity,question.text,null,question.id,{anchor:`question:${question.id}`});
  app.composer.text=text ?? (app.composer.text || `About: ${question.text}\n\n`);saveDraft(replyDraftKey(app.composer),app.composer.text);renderComposer();scheduleReplyPreview();
}

function questionActions(a, question) {
  app.questionDrafts ||= {};
  const input=h("input",{type:"text","aria-label":`Reply to ${displayName(a)}`,"data-reading-key":`draft:${question.id}`,
    on:{input:e=>{app.questionDrafts[question.id]=e.target.value;saveDraft(`question:${question.id}`,e.target.value);},
      keydown:e=>{if(e.key==='Enter' && !e.isComposing){e.preventDefault();sendQuestionReply(question,input.value);}}}});
  input.value=app.questionDrafts[question.id] || readDraft(`question:${question.id}`);
  const labels=UI.easy_reply.labels;
  // A terminal dialog offers its own choices; a plain Yes or No may not map to one of them.
  const options=question.terminal && question.options?.length ? question.options
    : [...new Set([...(question.options || []),labels.yes,labels.no])];
  return [h("div",{class:"quick-replies"},options.map(option=>h("span",{class:"quick-choice"},
    h("button",{class:"btn quick-send",type:"button",text:option,disabled:!!app.replySending?.[question.id],
      on:{click:()=>sendQuestionReply(question,option)}}),
    h("button",{class:"btn quiet quick-edit",type:"button",text:"✎","aria-label":`${labels.edit}: ${option}`,
      on:{click:()=>draftQuestionReply(question,option)}}))),
    h("button",{class:"btn",type:"button",text:labels.other,on:{click:()=>draftQuestionReply(question)}})),
    question.terminal ? h("p",{class:"muted small terminal-hint",text:UI.question_preview.labels.choose}) : null,
    app.composer?.questionId===question.id ? null : h("div",{class:"question-reply"},input,h("button",{class:"btn",type:"button",text:labels.send,
      on:{click:()=>sendQuestionReply(question,input.value)}}))];
}

// v0.9.5: the image a question names. The server decides what may be shown
// (question_images.py); the card shows its thumbnail or "Preview not available".
function shortPath(path) {
  const max=UI.question_preview.short_path;
  if (path.length<=max) return path;
  const parts=path.split("/"), keep=path.startsWith("~") ? 2 : 3, head=parts.slice(0,keep).join("/"), tail=parts.slice(keep);
  let short=`${head}/…/${tail.at(-1)}`;
  for (let i=tail.length-2;i>0;i--) {
    const next=`${head}/…/${tail.slice(i).join("/")}`;
    if (next.length>max) break;
    short=next;
  }
  return short.length<path.length ? short : path;
}

function questionImage(a, q) {
  const image=q.image;
  if (!image) return null;
  const labels=UI.question_preview.labels;
  app.failedImages ||= new Set();
  const failed=image.available && app.failedImages.has(image.id);
  // Full size opens in the Latest visuals viewer.
  const item={url:image.url,name:image.name,video:false,agent:displayName(a),at:q.at,relative_path:image.path,path:image.path,identity:a.identity};
  const preview=image.available && !failed
    ? h("button",{class:"question-thumb",type:"button","aria-label":`${labels.open}: ${image.name}`,on:{click:()=>openVisual(item)}},
      h("img",{src:image.thumbnail,alt:image.name,on:{error:()=>{app.failedImages.add(image.id);render(true);}}}))
    : h("div",{class:"question-thumb unavailable",role:"note"},h("span",{class:"question-thumb-title",text:labels.unavailable}),
      h("span",{class:"muted small",text:failed ? UI.question_preview.reasons.failed : image.reason}));
  return h("div",{class:"question-image","data-reading-key":`question-image:${q.id}`},preview,
    h("details",{class:"question-path","data-reading-key":`question-path:${q.id}`},
      h("summary",{title:image.path},h("span",{class:"muted small",text:labels.file})," ",h("span",{class:"path-short",text:shortPath(image.path)})),
      h("p",{class:"path-full",text:image.path})));
}

function questionCard(a, q=a.question) {
  if (!q) return h("article",{hidden:true});
  const about=q.about?agentById(q.about):null;
  return h("article",{class:"question-card","data-reading-key":`question:${q.id}`,"data-agent-identity":a.identity},
    h("div",{class:"reply-row-head"},h("a",{class:"route-who",href:hashLink("agent", a.identity)},accountIcon(a.icon,{size:14,text:false}),displayName(a)+(about?` · about ${displayName(about)}`:""),modelBadge(a)),
      h("span",{class:"chips"},terminalButton(a,{label:false,cls:"quiet icon-only"}),easyReplyButton(a,{question:q,anchor:`question:${q.id}`}))),
    h("p",{class:"muted small",text:q.reason}),h("blockquote",{class:"evidence-quote"},mdLine(q.text,0,true)),questionImage(a,q),
    recipientChip(replyRecipient(a)),questionActions(a,q),
    replySlot(`question:${q.id}`,a));
}

// ---------------------------------------------------------------- asks (v0.9.6)
// One plain row per question in the agent's message (agent.asks, contract:
// docs/briefs/V0.9.6-ITEMS-CONTRACT.md). Each row answers on its own through
// POST asks_answer; the others stay open. A terminal permission dialog keeps
// the 0.9.5 question card (numbered choices, image preview).

function askText(key, values = {}) {
  return Object.entries(values).reduce((text, [k, v]) => text.replaceAll(`{${k}}`, v), UI.ask_items.labels[key]);
}

function asksOf(a) {
  return a?.asks && ["questions", "maybe", "none"].includes(a.asks.state) ? a.asks : null;
}

// The asks that change a row or card: "maybe", "none", or question items other than a
// terminal permission dialog (that one keeps the 0.9.5 card and row).
function asksShown(a) {
  const s = asksOf(a);
  return s && (s.state !== "questions" || askItems(a).length) ? s : null;
}

function askItems(a) {
  const s = asksOf(a);
  return s?.state === "questions" ? (s.items || []).filter((i) => i.kind !== "permission") : [];
}

// The server's answer wins; until it arrives, this session's own.
function askAnswer(item) {
  return item.answered || app.askAnswers?.[item.id] || null;
}

function askOpen(item) {
  const done = askAnswer(item);
  return !done || done.delivery === "failed";
}

function openAskItems(a) {
  return askItems(a).filter(askOpen);
}

// "at 11:42", "yesterday at 22:02", "on 15 Nov at 09:10": the device's clock
// format for the time, English words.
function replyWhen(at) {
  const d = new Date(at * 1000), today = new Date();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return askText("at", { time });
  if (d.toDateString() === yesterday.toDateString()) return askText("yesterday", { time });
  return askText("on", { date: `${d.getDate()} ${UI.ask_items.months[d.getMonth()]}`, time });
}

function repliedText(reply, a = null) {
  const text = mdCut(mdInline(String(reply.text || "").replace(/\s+/g, " ")), UI.ask_items.cut.reply).map((s) => s.text).join("");
  const when = replyWhen(reply.at);
  return reply.to && a && reply.to !== displayName(a) ? askText("replied_to", { text, to: reply.to, when }) : askText("replied", { text, when });
}

function askRoute(a, item) {
  const asker = item.asked_by?.name || displayName(a), to = item.answer_to?.name || asker;
  return item.answer_to?.where ? askText("route_where", { asker, to, where: item.answer_to.where }) : askText("route", { asker, to });
}

// v0.9.7: the route sentence with each side's account icon inline (asked_by.icon, answer_to.icon).
// A side without a recorded account shows its name only. The plain sentence stays as the title.
function askRouteNode(a, item) {
  const asker = item.asked_by?.name || displayName(a), to = item.answer_to?.name || asker;
  const askerIcon = item.asked_by ? item.asked_by.icon : a.icon;
  const sides = { asker: [askerIcon, asker], to: [item.answer_to?.name ? item.answer_to.icon : askerIcon, to],
    where: [null, item.answer_to?.where || ""] };
  const key = item.answer_to?.where ? "route_where" : "route";
  const parts = UI.ask_items.labels[key].split(/(\{\w+\})/).filter(Boolean).map((part) => {
    const side = sides[part.slice(1, -1)];
    if (!side) return part;
    const mark = accountIcon(side[0], { size: 14, text: false });
    return mark ? h("span", { class: "route-who" }, mark, side[1]) : side[1];
  });
  return h("p", { class: "ask-route", title: askRoute(a, item) }, ...parts);
}

function askCount(a) {
  const items = askItems(a), open = openAskItems(a).length;
  if (open === items.length) return items.length === 1 ? UI.ask_items.labels.count_one : askText("count", { n: items.length });
  return askText("answered", { done: items.length - open, n: items.length });
}

function askDraft(item) {
  app.askDrafts ||= {};
  return app.askDrafts[item.id] ||= { choice: null };
}

// What Send sends: the chosen choice, the typed words, or both.
function askReplyText(item) {
  const choice = (item.choices || []).find((c) => c.value === askDraft(item).choice);
  const typed = readDraft(`ask:${item.id}`).trim();
  return choice && typed ? `${choice.value}: ${typed}` : choice ? choice.value : typed;
}

function updateAskSend(item) {
  const button = document.querySelector(`[data-reading-key="ask:${CSS.escape(item.id)}"] .ask-send`);
  if (!button) return;
  const choice = (item.choices || []).find((c) => c.value === askDraft(item).choice);
  button.disabled = !!app.askSending?.[item.id] || !askReplyText(item);
  button.textContent = app.askSending?.[item.id] ? UI.ask_items.labels.sending
    : choice && !readDraft(`ask:${item.id}`).trim() ? askText("send_choice", { choice: choice.label }) : UI.ask_items.labels.send;
}

async function sendAsk(a, item) {
  const text = askReplyText(item);
  if (!text || app.askSending?.[item.id]) return;
  (app.askSending ||= {})[item.id] = true;
  updateAskSend(item);
  try {
    const result = await api(UI.api.asks_answer, { method: "POST", body: { item: item.id, text } });
    const done = result?.answered || { text, at: result?.at || result?.sent_at || now(), delivery: result?.delivery || result?.status || "sent" };
    // A failed delivery stays on the row with its reason; the controls stay open.
    (app.askAnswers ||= {})[item.id] = done.delivery === "failed" ? { ...done, error: result?.error || done.error } : done;
    if (done.delivery === "failed") return;
    saveDraft(`ask:${item.id}`, "");
    delete app.askDrafts?.[item.id];
    if (document.activeElement?.closest(`[data-reading-key="ask:${CSS.escape(item.id)}"]`)) document.activeElement.blur();
    await refresh(true);
  } catch (err) {
    flash(`Not sent: ${err.message}`);
  } finally {
    delete app.askSending[item.id];
    render(true);
  }
}

function askControls(a, item, failed) {
  const labels = UI.ask_items.labels, draft = askDraft(item), sending = !!app.askSending?.[item.id];
  const choices = item.choices || [];
  const input = h("input", { type: "text", class: "ask-input", maxlength: String(UI.ask_items.text_max), enterkeyhint: "send",
    "data-reading-key": `ask-input:${item.id}`, "aria-label": `${labels.type_only}: ${mdPlain(item.text)}`,
    placeholder: choices.length ? labels.type : labels.type_only,
    on: { input: (e) => { saveDraft(`ask:${item.id}`, e.target.value); updateAskSend(item); },
      keydown: (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); sendAsk(a, item); } } } });
  input.value = readDraft(`ask:${item.id}`);
  const chosen = choices.find((c) => c.value === draft.choice);
  const send = h("button", { class: "btn primary ask-send", type: "submit", disabled: sending || !askReplyText(item),
    text: sending ? labels.sending : chosen && !input.value.trim() ? askText("send_choice", { choice: chosen.label }) : labels.send });
  return h("div", { class: "ask-controls" },
    choices.length ? h("div", { class: "ask-choices", role: "radiogroup", "aria-label": labels.choices },
      choices.map((c) => h("button", { class: "btn ask-choice", type: "button", role: "radio", "aria-checked": String(draft.choice === c.value),
        disabled: sending, text: c.label,
        on: { click: () => { draft.choice = draft.choice === c.value ? null : c.value; render(true); } } }))) : null,
    h("form", { class: "ask-answer", on: { submit: (e) => { e.preventDefault(); sendAsk(a, item); } } }, input, send),
    failed ? h("p", { class: "error small", role: "alert", text: askText("failed", { error: failed.error || "delivery failed" }) }) : null);
}

// item.text may be reworded (by rules or AI); the agent's exact words (item.quote)
// then stay visible in the row, as a quiet quote under the question.
function askQuoteDiffers(item) {
  const plain = (t) => mdPlain(t).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  return !!item.quote && plain(item.quote) !== plain(item.text);
}

function askRow(a, item) {
  const done = askAnswer(item), failed = done?.delivery === "failed";
  return h("li", { class: `ask-item${done && !failed ? " is-answered" : ""}`, "data-reading-key": `ask:${item.id}`, "data-agent-identity": a.identity },
    h("p", { class: "ask-text" }, mdLine(item.text, 0, true),
      item.urgency === "later" ? h("span", { class: "ask-later", title: UI.ask_items.labels.for_later_title, text: UI.ask_items.labels.for_later }) : null),
    askQuoteDiffers(item) ? h("blockquote", { class: "ask-quote", title: UI.ask_items.labels.quote },
      h("span", { class: "sr-only", text: `${UI.ask_items.labels.quote}: ` }),
      mdBlock(item.quote, { limit: UI.ask_items.cut.card, place: `quote:${item.id}` })) : null,
    item.context ? h("p", { class: "ask-context" }, mdLine(item.context, 0, true)) : null,
    askRouteNode(a, item),
    done && !failed ? h("p", { class: "ask-replied", role: "status" },
      h("span", { class: "ask-tick", "aria-hidden": "true", text: "✓" }), " ", repliedText(done),
      done.delivery === "queued" ? h("span", { class: "ask-queued", text: ` ${UI.ask_items.labels.queued}` }) : null)
      : askControls(a, item, failed ? done : null));
}

function askOriginal(a, s) {
  if (!s.original) return null;
  return h("details", { class: "ask-original", "data-reading-key": `ask-original:${a.identity}` },
    h("summary", { text: UI.ask_items.labels.show_original }),
    h("p", { class: "ask-original-from", text: askText("original_from", { name: displayName(a) }) }),
    mdBlock(s.original, { cls: "ask-original-text" }));
}

// The asks of one agent: question rows, or a calm "may want something" or
// "Done, not reviewed". With `head`, the agent's name leads (Questions block).
function asksSection(a, { head = false } = {}) {
  const s = asksOf(a);
  if (!s) return null;
  const labels = UI.ask_items.labels;
  const who = head ? h("div", { class: "asks-head" },
    h("a", { class: "asks-who route-who", href: hashLink("agent", a.identity) }, accountIcon(a.icon, { size: 14, text: false }), displayName(a), modelBadge(a)),
    s.state === "questions" ? h("span", { class: "asks-count", text: askCount(a) }) : null,
    terminalButton(a, { label: false, cls: "quiet icon-only" })) : null;
  if (s.state === "questions") {
    const items = askItems(a);
    if (!items.length) return null;
    return h("section", { class: "asks", "data-reading-key": `asks:${a.identity}` }, who,
      head ? null : h("p", { class: "asks-count", text: askCount(a) }),
      h("ol", { class: "ask-list" }, items.map((item) => askRow(a, item))),
      askOriginal(a, s));
  }
  if (s.state === "maybe") {
    return h("section", { class: "asks asks-maybe", "data-reading-key": `asks:${a.identity}` }, who,
      h("p", { class: "asks-state", text: labels.maybe }),
      h("p", { class: "ask-route", text: labels.maybe_hint }),
      mdBlock(s.original, { limit: UI.ask_items.cut.card, place: `maybe:${a.identity}` }));
  }
  return h("section", { class: "asks asks-none", "data-reading-key": `asks:${a.identity}` }, who,
    h("p", { class: "asks-state", text: labels.none }),
    s.summary ? mdBlock(s.summary, { limit: UI.ask_items.cut.card, place: `done:${a.identity}` }) : null,
    askOriginal(a, s));
}

// Question entries for the Questions block: one per agent with question items,
// else the 0.9.5 question (terminal dialogs). Agents stay listed in this
// session after Alex answered their last item, so the answer stays visible.
function questionEntries(view) {
  const out = [], seen = new Set();
  const byId = (id) => view.agents.find((a) => a.identity === id);
  for (const q of view.questions || []) {
    const a = byId(q.identity);
    if (!a) continue;
    if (askItems(a).length) {
      if (!seen.has(a.identity)) { seen.add(a.identity); out.push({ agent: a, asks: true, keep_open: q.keep_open }); }
    } else out.push({ agent: a, question: q, keep_open: q.keep_open });
  }
  for (const a of view.agents) {
    if (seen.has(a.identity) || !askItems(a).length) continue;
    if (openAskItems(a).length || askItems(a).some((i) => app.askAnswers?.[i.id])) { seen.add(a.identity); out.push({ agent: a, asks: true }); }
  }
  return out;
}

function openQuestionCount(entries) {
  return entries.reduce((n, e) => n + (e.asks ? openAskItems(e.agent).length : 1), 0);
}

function questionEntryCard(e) {
  return e.asks ? h("article", { class: "question-card asks-card", "data-reading-key": `asks-card:${e.agent.identity}`, "data-agent-identity": e.agent.identity },
    asksSection(e.agent, { head: true })) : questionCard(e.agent, e.question);
}

// List, Notifications and the graph show the card only when a question is open;
// the calm answer is the status header. #/questions (compact=false) always shows it.
function questionsBlock(view, compact=true) {
  const entries=questionEntries(view);
  if (compact && !entries.length) return h("section",{class:"questions-for-you",id:"questions-for-you","data-reading-key":"questions-for-you",hidden:true});
  const limit=UI.watch_closely.compact_question_limit;
  const pinned=entries.filter(e=>e.keep_open);
  const visible=compact ? [...pinned,...entries.filter(e=>!e.keep_open).slice(0,Math.max(0,limit-pinned.length))] : entries;
  return h("section",{class:"card questions-for-you",id:"questions-for-you","data-reading-key":"questions-for-you"},
    h("h2",{class:"sub",text:`Questions for you · ${openQuestionCount(entries)}`}),
    visible.length?visible.map(questionEntryCard):h("p",{text:"No open questions for you."}),
    entries.length>visible.length?h("a",{class:"btn",href:hashLink("questions"),text:`+${entries.length-visible.length} more`}):null);
}

function watchedQuestions(a) {
  const questions=(app.view.questions || []).filter(q=>q.keep_open && q.identity===a.identity);
  return questions.length ? h("div",{class:"watched-questions","data-reading-key":`watched-questions:${a.identity}`},
    questionsBlock({...app.view,questions,agents:app.view.agents.filter(x=>x.identity===a.identity)},false)) : null;
}

function subagentFold(parent, {onToggle, open=false}={}) {
  if (!parent.finished_subagents?.length) return null;
  return h("details",{class:"subagent-fold","data-reading-key":`subagents:${parent.identity}`,open,
    on:{toggle:event=>onToggle?.(event.currentTarget.open)}},
    h("summary",{},h("span",{class:"fold-parent",text:displayName(parent)})," · ",h("span",{class:"fold-label",text:parent.finished_subagents_label})),
    h("ul",{class:"plain"},parent.finished_subagents.map(id=>{
      const child=agentById(id);
      return child ? h("li",{},h("a",{class:"chip",href:hashLink("agent",id),text:displayName(child)})) : null;
    })));
}

function subagentFolds(view, options={}) {
  return h("div",{class:"subagent-folds","data-reading-key":"subagent-folds"},
    view.agents.filter(a=>a.finished_subagents?.length).map(a=>subagentFold(a,options)));
}

function attentionItems(view) {
  return (view.agents || []).filter((a) => !a.folded && agentTier(a));
}

function attentionStrip(view) {
  const items = attentionItems(view).filter(a=>!a.quota_stop);
  if (!items.length) return null;
  const you = items.filter((a) => agentTier(a) === "waiting_on_you").length;
  const head = you ? `${you} waiting on you` : `${items.length} to look at`;
  const groups = tierOrder().map((tier) => [tier, items.filter((a) => agentTier(a) === tier)]).filter(([, list]) => list.length);
  // The status header already gives the count; the card lists the items, calm.
  return h("section", { class: "card attention", "aria-label": `Needs a look: ${head}`, "data-reading-key":"attention" },
    groups.map(([tier, list]) => { const hint = groupReadHint(list); return h("div", { class: "attention-group", "data-reading-key":`attention:${tier}` },
      h("h2", { class: "attention-tier", text: `${tierHead(tier)} · ${list.length}` }),
      hint ? h("p", { class: "muted small read-state-hint section-hint", text: hint }) : null,
      h("ul", { class: "attention-list" },
        list.map((a) => attentionRow(a, { sectionHint: hint })))); }));
}

// Stacked row (PH-5): name and age, the reason on one line, then the AI headline or
// the quoted snippet (two lines at most), the workspace; Summarize below when there
// is no current AI headline yet.
// The stored phone notification title for this agent's current event, if any.
function notificationTitle(a) {
  if (!a.event_id) return null;
  const note = (app.view?.notifications?.items || []).find((n) => n.id === a.event_id || n.group_ids?.includes(a.event_id));
  return note?.what || null;
}

// One reason line. A Waiting-on-a-coordinator row never repeats a state label
// such as "Finished, not reviewed" that the tier heading already contradicts.
function attentionReason(a) {
  const tier = agentTier(a);
  if (a.reason) return a.reason;
  if (tier === "waiting_on_coordinator") return a.tier_label || tierHead(tier);
  return a.tier_label || tierHead(tier) || a.label;
}

// The dot: red only while Alex has an open question; a blocked screen in another
// tier is caution; "may want something" and "Done, not reviewed" are never red.
function attentionHue(a) {
  const s = asksOf(a);
  if (s?.state === "maybe") return "caution";
  if (s?.state === "none") return "done";
  if (s?.state === "questions" && askItems(a).length && !openAskItems(a).length) return "waiting";
  return agentTier(a) === "waiting_on_you" ? "needs_you" : a.state === "needs_you" ? "caution" : a.state;
}

// What the row says under the reason: the first open question, the calm asks
// state, or the agent's own words, cut at a word boundary (the row opens them).
function attentionDetail(a, headline) {
  const s = asksOf(a), cut = UI.ask_items.cut.line;
  const first = openAskItems(a)[0];
  if (first) return h("q", { class: "detail question-quote" }, mdLine(first.text, cut));
  if (s?.state === "maybe") return h("span", { class: "detail" }, mdLine(s.original, cut));
  if (s?.state === "none" && (s.summary || s.original)) return h("span", { class: "detail" }, mdLine(s.summary || s.original, cut));
  return [a.question && !askItems(a).length ? h("q", { class: "detail question-quote" }, mdLine(a.question.text, cut)) : null,
    headline ? h("span", { class: "detail ai-line" }, h("span", { class: "ai-tag", title: "AI summary", text: "AI" }), " ", mdLine(headline, cut))
      : h("span", { class: "detail" }, summaryLine(a), a.snippet ? [" · ", h("q", { class: "snippet" }, mdLine(a.snippet, cut))] : null)];
}

function attentionReasonText(a) {
  const s = asksOf(a);
  if (s?.state === "questions" && askItems(a).length) return askCount(a);
  if (s?.state === "maybe") return UI.ask_items.labels.maybe;
  if (s?.state === "none") return UI.ask_items.labels.none;
  return attentionReason(a);
}

function attentionRow(a, { sectionHint = null } = {}) {
  const s = a.summary;
  const title = notificationTitle(a);
  const headline = title || (s && s.status === "complete" && s.current && s.headline);
  const asks = askItems(a).length > 0;
  return h("li", {"data-reading-key":`attention:${a.identity}:${a.event_id || ""}`,"data-reply-anchor":`attention:${a.identity}`},
    h("a", { class: "att-row", href: hashLink('agent', a.identity) },
      h("i", { class: `dot state s-${attentionHue(a)}` }),
      h("span", { class: "who" }, displayName(a), accountIcon(a.icon, { size: 14, text: false }), modelBadge(a), watchBadge(a)),
      h("span", { class: "age", text: a.event_at ? ago(a.event_at) : "" }),
      title && !asksShown(a) ? h("span", { class: "att-headline" }, mdLine(title)) : null,
      h("span", { class: `reason${title && !asksShown(a) ? " muted" : ""}`, text: attentionReasonText(a) }),
      title && !asksShown(a) ? (a.question ? h("q",{class:"detail question-quote"},mdLine(a.question.text, UI.ask_items.cut.line)) : null) : attentionDetail(a, headline),
      h("span", { class: "ws", text: a.workspace_label })),
    h("div", {class:"attention-row-actions"},
      // Question items are answered in their own rows (Questions for you, agent page).
      agentTier(a)==='waiting_on_you' && !asks?easyReplyButton(a,{question:a.question}):null,
      headline || asksShown(a) ? null : summarizeButton(a, { class: "quiet small" }), terminalButton(a, { cls: "quiet" }), openInHerdrButton(a),
      attentionMarkRead(a, { sectionHint })),replySlot(`attention:${a.identity}`,a));
}

function canMarkAgentRead(a) {
  return a.event_id && !a.acked && app.view.features?.acknowledge && a.read_action?.available!==false;
}

function readHint(a) {
  return app.view.features?.[UI.features.desktop_focus] ? a.read_action?.reason : (a.read_action?.remote_reason || a.read_action?.reason);
}

// v0.9.8.6: the "Clears when ..." hint once under a section heading when every row in it says the
// same, instead of on each row.
function groupReadHint(list) {
  const hints = [...new Set(list.map(readHint))];
  return hints.length === 1 && hints[0] ? hints[0] : null;
}

function attentionMarkRead(a, { sectionHint = null } = {}) {
  const hint = readHint(a);
  if (hint && hint === sectionHint) return null;
  if (hint) return h("span",{class:"muted small read-state-hint",text:hint});
  return canMarkAgentRead(a)
    ? inlineMarkRead(`agent:${a.identity}:${a.event_id}`, () => ack(a.event_id, a.identity, {navigate:false})) : null;
}

// Workspaces with someone waiting on Alex first, then by items to look at; otherwise stable.
function needYouFirst(list) {
  return list.map((ws, i) => [ws, i]).sort(([x, i], [y, j]) =>
    ((y.counts?.waiting_on_you || 0) - (x.counts?.waiting_on_you || 0)) || ((y.attention || 0) - (x.attention || 0)) || (i - j)).map(([ws]) => ws);
}

function renderOverview() {
  const view = app.view;
  if (!view.workspaces.length) {
    const box = emptyState();
    const quotas = sectionShown("quotas") ? quotaStrip(view) : null;
    if (quotas) box.insertBefore(quotas, box.firstChild);
    return box;
  }
  const total = view.agents.filter((a) => a.present && !a.folded).length;
  const working = view.counts.state_working || 0;
  // The main information blocks follow Alex's order (Arrange); the workspaces come after them.
  const blocks = {
    needs_action: [questionsBlock(view), quotaStopBlock(view), attentionStrip(view), subagentFolds(view)],
    quotas: quotaStrip(view),
    status_reports: statusReportsBlock(view),
    latest_visuals: visualsStrip("overview", ""),
    other_updates: otherUpdatesBlock(view),
  };
  return h("div", {"data-reading-key":"overview"},
    statusHeader(view),
    h("div", {class:"arrange-bar","data-reading-key":"arrange-bar"}, arrangeButton()),
    arrangedBlocks(blocks),
    h("p", { class: "overview-line", text: `${view.workspaces.length} workspace${view.workspaces.length === 1 ? "" : "s"} · ${total} agent${total === 1 ? "" : "s"} · ${working} working` }),
    cleanupButton(null),
    h("div", { class: "grid two" }, needYouFirst(activeWorkspaces(view)).map(workspaceCard)),
    emptyWorkspaceGroup(view),
    hiddenBlocksNote(blocks));
}

function emptyState() {
  const box = h("div", {});
  box.appendChild(h("section", { class: "card empty" },
    h("h1", { text: "Pick the workspaces to watch" }),
    h("p", { class: "muted", text: "Nothing is watched until you switch it on. Only the workspaces you choose show up here or send alerts." })));
  box.appendChild(h("h2", { class: "section", text: "Your Herdr workspaces" }));
  box.appendChild(chooserCard());
  return box;
}

// ---------------------------------------------------------------- arrange (v0.9.4)
// Alex's own order for accounts/services and the main information blocks. The
// server keeps it in settings (view.layout), so the phone, the desktop window
// and the bar panel read the same lists. Nothing reorders it by itself.

function sectionLabel(id) {
  return UI.arrange.sections.find((s) => s.id === id)?.label || id;
}

function arrangeLayout() {
  const l = app.view?.layout || {}, ids = UI.arrange.sections.map((s) => s.id);
  const order = (l.section_order || []).filter((id) => ids.includes(id));
  const hideable = (id) => UI.arrange.sections.find((s) => s.id === id)?.hideable;
  return {account_order: [...(l.account_order || [])], account_pinned: [...(l.account_pinned || [])],
          account_hidden: [...(l.account_hidden || [])], section_order: [...order, ...ids.filter((id) => !order.includes(id))],
          section_hidden: (l.section_hidden || []).filter(hideable)};
}

function sectionShown(id, layout = arrangeLayout()) {
  return !layout.section_hidden.includes(id);
}

// Accounts in Alex's order: pinned first, then his order, then new ones in the
// order the records give. A nearly full account may be flagged, never moved.
function arrangedAccounts(accounts, layout = arrangeLayout()) {
  const pos = (id) => { const i = layout.account_order.indexOf(id); return i < 0 ? Infinity : i; };
  const pinned = (id) => (layout.account_pinned.includes(id) ? 0 : 1);
  return accounts.map((a, i) => ({a, i})).sort((x, y) =>
    (pinned(x.a.id) - pinned(y.a.id)) || (pos(x.a.id) - pos(y.a.id)) || (x.i - y.i)).map((x) => x.a);
}

function accountShown(id, layout = arrangeLayout()) {
  return !layout.account_hidden.includes(id);
}

// The blocks that are shown, in Alex's order, each root marked with its section.
function arrangedBlocks(blocks, layout = arrangeLayout()) {
  return layout.section_order.filter((id) => id in blocks && sectionShown(id, layout)).flatMap((id) =>
    [blocks[id]].flat().filter(Boolean).map((node) => { node.dataset.section = id; return node; }));
}

// One muted line names hidden blocks that have content, so nothing is lost.
function hiddenBlocksNote(blocks, counts = {}, layout = arrangeLayout()) {
  const hidden = layout.section_hidden.filter((id) => [blocks[id]].flat().some(Boolean));
  if (!hidden.length) return null;
  const names = hidden.map((id) => (counts[id] ? `${sectionLabel(id)} (${counts[id]})` : sectionLabel(id))).join(", ");
  return h("p", {class:"muted small arrange-hidden","data-reading-key":"arrange-hidden"},
    `${UI.arrange.labels.hidden_blocks} ${names}`, arrangeButton());
}

function statusReportsBlock(view) {
  const leads = view.agents.filter((a) => a.is_coordinator && a.present !== false && !a.folded);
  if (!leads.length) return null;
  const L = UI.arrange.labels, W = UI.watch_closely.labels;
  return h("section", {class:"card status-reports","data-reading-key":"status-reports"},
    h("h2", {class:"sub",text:`${sectionLabel("status_reports")} · ${leads.length}`}),
    h("ul", {class:"plain report-list"}, leads.map((a) => {
      const row = a.status_report, report = row?.summary;
      const line = report ? [report.status, row.out_of_date ? W.out_of_date : null].filter(Boolean).join(" · ")
        : row && row.status !== "complete" ? (row.status === "failed" ? L.report_failed : W.refreshing) : L.no_report;
      return h("li", {"data-reading-key":`report:${a.identity}`},
        h("a", {class:"report-row",href:hashLink("report", a.identity)},
          h("span", {class:"who",text:displayName(a)}), h("span", {class:"muted small",text:line})));
    })));
}

// Unread updates that need no action, folded; the full list is on Notifications.
function otherUpdatesBlock(view) {
  const items = (view.notifications?.items || []).filter((n) => n.is_group_latest !== false && !n.read && !notifActionable(n));
  if (!items.length) return null;
  return h("details", {class:"notif-other overview-other","data-reading-key":"overview-other"},
    h("summary", {text:`${sectionLabel("other_updates")} (${items.length})`}), items.map((n) => notificationCard(n, false)));
}

function arrangeButton() {
  return h("button", {class:"btn quiet arrange-open",type:"button","aria-haspopup":"dialog",text:UI.arrange.labels.open,
    on:{click:(event) => openArrange(event.currentTarget)}});
}

function openArrange(opener) {
  app.arrange = {draft: arrangeLayout(), status: "", focus: null, drag: null};
  app.arrangeOpener = opener || null;
  renderArrange();
  $("arrange-sheet").querySelector(".arrange")?.focus();
}

function closeArrange() {
  if (!app.arrange) return;
  if (arrangeChanged.timer) { clearTimeout(arrangeChanged.timer); arrangeChanged.timer = null; saveArrange(app.arrange.draft); }
  app.arrange = null;
  renderArrange();
  // The opener may have been patched away by a refresh; fall back to the first Arrange button.
  const opener = app.arrangeOpener?.isConnected ? app.arrangeOpener : document.querySelector(".arrange-open");
  opener?.focus();
}

// The account rows in the sheet: known accounts from the quota records.
function arrangeAccounts(draft) {
  return arrangedAccounts((app.view?.quotas?.accounts || []).map((a) => ({id: a.id, name: quotaName(a)})), draft);
}

// Unknown ids (no quota records now) keep their place at the end of the saved order.
function mergeOrder(shown, saved) {
  return [...shown, ...saved.filter((id) => !shown.includes(id))];
}

function arrangeDrop(kind, id, targetId, after) {
  const d = app.arrange?.draft;
  if (!d || id === targetId) return;
  const list = kind === "section" ? [...d.section_order] : arrangeAccounts(d).map((a) => a.id);
  if (!list.includes(id) || !list.includes(targetId)) return;
  if (kind === "account" && d.account_pinned.includes(id) !== d.account_pinned.includes(targetId)) return; // pinned stay first
  const rest = list.filter((x) => x !== id);
  rest.splice(rest.indexOf(targetId) + (after ? 1 : 0), 0, id);
  if (kind === "section") d.section_order = rest; else d.account_order = mergeOrder(rest, d.account_order);
  arrangeChanged();
}

function arrangeMove(kind, id, delta) {
  const d = app.arrange.draft;
  const list = kind === "section" ? d.section_order : arrangeAccounts(d).map((a) => a.id);
  const target = list[list.indexOf(id) + delta];
  app.arrange.focus = `${kind}:${id}:${delta < 0 ? "up" : "down"}`;
  if (target) arrangeDrop(kind, id, target, delta > 0);
}

function arrangeToggle(kind, key, id) {
  const d = app.arrange.draft;
  // Fix the shown order first: a pin goes to the end of the pinned group, an unpin to the top of the rest.
  if (kind === "account") d.account_order = mergeOrder(arrangeAccounts(d).map((a) => a.id), d.account_order);
  d[key] = d[key].includes(id) ? d[key].filter((x) => x !== id) : [...d[key], id];
  if (kind === "section") d.section_hidden = d.section_order.filter((x) => d.section_hidden.includes(x));
  app.arrange.focus = `${kind}:${id}:${key}`;
  arrangeChanged();
}

function arrangeChanged() {
  app.arrange.status = UI.arrange.labels.saving;
  renderArrange();
  clearTimeout(arrangeChanged.timer);
  arrangeChanged.timer = setTimeout(() => { arrangeChanged.timer = null; if (app.arrange) saveArrange(app.arrange.draft); }, 250);
}

// Saves run one after another, so the last change wins.
function saveArrange(draft) {
  const body = Object.fromEntries(Object.entries(draft).map(([key, value]) => [`layout.${key}`, [...value]]));
  app.arrangeQueue = (app.arrangeQueue || Promise.resolve()).then(async () => {
    const ok = await saveSettings(body);
    if (!app.arrange || app.arrange.draft !== draft || arrangeChanged.timer) return;
    if (!ok) app.arrange.draft = arrangeLayout();   // show what is really saved
    app.arrange.status = ok ? UI.arrange.labels.saved : "";
    renderArrange();
  });
  return app.arrangeQueue;
}

// The sheet lives outside #app: a live refresh never rebuilds it.
function renderArrange() {
  let root = $("arrange-sheet");
  if (!root) { root = h("div", {id:"arrange-sheet"}); document.body.appendChild(root); }
  if (!app.arrange) { root.replaceChildren(); root.hidden = true; return; }
  root.hidden = false;
  updateReadingView(root, arrangeSheet(app.arrange));
  const key = app.arrange.focus;
  app.arrange.focus = null;
  const target = key && [...root.querySelectorAll("[data-arrange-focus]")].find((el) => el.dataset.arrangeFocus === key);
  if (target) (target.disabled ? target.closest("li").querySelector("button:not(:disabled)") : target)?.focus();
}

function arrangeSheet(state) {
  const d = state.draft, L = UI.arrange.labels;
  // Drag on the desktop (no touch screen, wide window); up and down everywhere.
  const fine = !window.matchMedia?.("(pointer: coarse)").matches && !!window.matchMedia?.("(min-width: 700px)").matches;
  const sections = d.section_order.map((id) => UI.arrange.sections.find((s) => s.id === id)).filter(Boolean);
  const accounts = arrangeAccounts(d), pinned = (a) => !!a && d.account_pinned.includes(a.id);
  return h("div", {class:"modal-backdrop arrange-backdrop","data-reading-key":"arrange",
      on:{click:(e) => { if (e.target === e.currentTarget) closeArrange(); }, keydown:(e) => { if (e.key === "Escape") closeArrange(); }}},
    h("div", {class:"modal arrange",role:"dialog","aria-modal":"true","aria-labelledby":"arrange-title",tabindex:"-1"},
      h("h2", {id:"arrange-title",text:L.title}),
      h("p", {class:"muted small",text:L.intro}),
      h("h3", {class:"sub",text:L.sections}),
      h("ol", {class:"arrange-list","data-reading-key":"arrange-sections"}, sections.map((s, i) => arrangeRow({
        kind:"section", id:s.id, name:s.label, fine, hideable:s.hideable, hidden:d.section_hidden.includes(s.id),
        first:i === 0, last:i === sections.length - 1}))),
      h("h3", {class:"sub",text:L.accounts}),
      accounts.length ? h("ol", {class:"arrange-list","data-reading-key":"arrange-accounts"}, accounts.map((a, i) => arrangeRow({
        kind:"account", id:a.id, name:a.name, fine, hideable:true, hidden:d.account_hidden.includes(a.id), pinned:pinned(a),
        first:i === 0 || pinned(a) !== pinned(accounts[i - 1]), last:i === accounts.length - 1 || pinned(a) !== pinned(accounts[i + 1])})))
        : h("p", {class:"muted",text:L.no_accounts}),
      h("div", {class:"actions"},
        h("span", {class:"muted small arrange-status",role:"status",text:state.status || ""}),
        h("button", {class:"btn primary",type:"button",text:L.done,on:{click:closeArrange}}))));
}

function arrangeRow(r) {
  const L = UI.arrange.labels;
  const tags = [r.pinned ? L.pinned : null, r.hidden ? L.hidden : null].filter(Boolean).join(" · ");
  const drag = r.fine ? {
    dragstart:(e) => { app.arrange.drag = {kind:r.kind, id:r.id}; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", r.id); },
    dragover:(e) => { const g = app.arrange?.drag; if (g && g.kind === r.kind && g.id !== r.id) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; } },
    drop:(e) => {
      e.preventDefault();
      const g = app.arrange?.drag, box = e.currentTarget.getBoundingClientRect();
      if (g && g.kind === r.kind) arrangeDrop(r.kind, g.id, r.id, e.clientY > box.top + box.height / 2);
    },
    dragend:() => { if (app.arrange) app.arrange.drag = null; },
  } : null;
  const button = (cls, text, label, focus, onClick, disabled = false) => h("button", {class:`btn quiet ${cls}`,type:"button",text,
    "aria-label":label,disabled,"data-arrange-focus":`${r.kind}:${r.id}:${focus}`,on:{click:onClick}});
  return h("li", {class:`arrange-row${r.hidden ? " is-hidden" : ""}${r.pinned ? " is-pinned" : ""}`,"data-reading-key":`${r.kind}:${r.id}`,
      "data-arrange-id":r.id, draggable:r.fine ? "true" : null, on:drag},
    r.fine ? h("span", {class:"arrange-grip","aria-hidden":"true",title:L.drag,text:"⠿"}) : null,
    h("span", {class:"arrange-name"}, h("span", {text:r.name}), tags ? h("span", {class:"muted small",text:tags}) : null),
    r.kind === "account" ? button("arrange-pin", r.pinned ? L.unpin : L.pin, `${r.pinned ? L.unpin : L.pin}: ${r.name}`, "account_pinned",
      () => arrangeToggle("account", "account_pinned", r.id)) : null,
    r.hideable ? button("arrange-hide", r.hidden ? L.show : L.hide, `${r.hidden ? L.show : L.hide}: ${r.name}`, `${r.kind}_hidden`,
      () => arrangeToggle(r.kind, `${r.kind}_hidden`, r.id))
      : h("span", {class:"muted small arrange-fixed",text:L.always_shown}),
    button("arrange-move", "↑", `${L.up}: ${r.name}`, "up", () => arrangeMove(r.kind, r.id, -1), r.first),
    button("arrange-move", "↓", `${L.down}: ${r.name}`, "down", () => arrangeMove(r.kind, r.id, 1), r.last));
}

// ---------------------------------------------------------------- notifications
// hashLink('notifications') lists recent notifications; "#/n/<id>" is the same list focused on one.
// Every notification (ntfy, desktop) links here. Data: GET /api/notifications.

const NOTIF_GONE = "That notification is no longer available.";

async function loadNotifications(focusId) {
  if (app.notifLoading) return;
  app.notifLoading = true;
  try {
    const data = await api(UI.api.notifications + "?limit=50");
    let items = data.items || [];
    if (focusId && !items.some((n) => String(n.id) === String(focusId))) {
      try { items = [await api(apiPath("notification", focusId)), ...items]; }
      catch (err) { if (err.message === "login required") throw err; app.notifGone = String(focusId); }
    }
    app.notifications = { items, unread: data.unread ?? items.filter((n) => !n.read).length, at: now() };
  } catch (err) {
    if (err.message !== "login required") app.notifications = { ...(app.notifications || { items: [] }), error: err.message, at: now() };
  } finally {
    app.notifLoading = false;
  }
  if (route().name === "notifications") render(true);
}

// Read notifications are hidden unless "Show read" is on (remembered per browser).
function showReadNotifications() {
  try { return localStorage.getItem("hat.notif.showRead") === "1"; } catch (_) { return false; }
}

function toggleShowRead() {
  try { localStorage.setItem("hat.notif.showRead", showReadNotifications() ? "0" : "1"); } catch (_) { /* private mode */ }
  render(true);
}

// One quiet control for compact notification and attention rows. Keep it outside
// their open link and keep in-flight state when a poll patches the row.
function inlineMarkRead(key, markRead) {
  return h("button", {class:"btn quiet notif-mark", type:"button", text:UI.labels.read, "aria-label":"Mark read",
    "data-reading-key":`read:${key}`, disabled:app.markingRead.has(key),
    on:{click:async event=>{
      event.preventDefault();
      event.stopPropagation();
      if (app.markingRead.has(key)) return;
      app.markingRead.add(key);
      event.currentTarget.disabled = true;
      try { await markRead(); }
      finally { app.markingRead.delete(key); render(true); }
    }}});
}

async function markNotificationRead(id) {
  // The List view shows rows from the view; the group ids are there too.
  const item=[...(app.notifications?.items || []),...(app.view?.notifications?.items || [])].find(n=>String(n.id)===String(id));
  const ids=item?.group_ids || [id];
  if (ids.map(String).includes(String(route().id))) history.replaceState(null, "", hashLink("notifications"));
  const failures=[];
  for (const eventId of ids) {
    try { await api(apiPath("event_ack", eventId), {method:"POST"}); }
    catch (err) { if (err.status!==404) failures.push(err.message); }
  }
  if (failures.length) flash(`Not marked read: ${failures[0]}`);
  await loadNotifications(route().id);
  await refresh(true);
}

async function markAllNotificationsRead() {
  try { await api(UI.api.notifications_read_all, { method: "POST", body: {} }); }
  catch (err) { flash(`Not marked read: ${err.message}`); }
  await loadNotifications(route().id);
  await refresh(true);
}

function unreadNotifications() {
  return (app.view && app.view.notifications && app.view.notifications.unread) || 0;
}

// Red only when an unread notification (or a live agent) is waiting on Alex.
function notificationsUrgent() {
  const items = (app.notifications && app.notifications.items) || [];
  if (items.some((n) => !n.read && n.tier === "waiting_on_you")) return true;
  return (app.view && app.view.agents || []).some((a) => !a.acked && agentTier(a) === "waiting_on_you");
}

function updateNotificationBadge(badge) {
  if (!badge) return;
  const unread = unreadNotifications();
  badge.hidden = !unread;
  badge.textContent = String(unread);
  badge.classList.toggle("urgent", unread > 0 && notificationsUrgent());
}

function gateLine(gate) {
  if (!gate) return null;
  const lead = { held: "Held back by the AI check", reworded: "Reworded by the AI check", sent: "Passed the AI check", unavailable: "AI check unavailable" }[gate.decision] || "AI check";
  return h("p", { class: "note notif-gate", text: gate.reason ? `${lead}: ${gate.reason}` : lead });
}

// Chips navigate ("go to"); buttons act. Theme v0.8.
function notificationLinks(n) {
  const id = n.agent && n.agent.identity;
  const out = [];
  if (id) {
    out.push(h("a", { class: "chip", href: hashLink('agent', id), text: "Agent" }));
    out.push(h("a", { class: "chip", href: hashLink("conversation", id), text: "Conversation" }));
    out.push(h("a", { class: "chip", href: hashLink("output", id), text: "Output" }));
  }
  if (n.coordinator && n.coordinator.identity)
    out.push(h("a", { class: "chip", href: hashLink('agent', n.coordinator.identity), text: `Coordinator: ${n.coordinator.name || "open"}` }));
  for (const l of n.links || []) {
    if (!l.href || !/^(https?:|#\/|\/)/.test(l.href)) continue;   // never javascript: or other schemes
    out.push(h("a", { class: "chip", href: l.href, target: l.href.startsWith("#") ? null : "_blank", rel: "noopener noreferrer",
      text: l.kind === "pr" ? (l.label || "PR") : `${{ branch: "Branch", file: "File" }[l.kind] || "Link"}: ${l.label || l.href}` }));
  }
  return out;
}

function notificationActions(n) {
  const agent = n.agent && n.agent.identity ? (agentById(n.agent.identity) || { identity: n.agent.identity }) : null;
  const live = agent && n.agent.present !== false && agentById(n.agent.identity);
  return [
    live && !['reply','terminal'].includes(n.next_action?.kind) ? easyReplyButton(agent,{eventId:n.id,anchor:`notification:${n.id}`,question:n.next_action?.question}) : null,
    live ? summarizeButton(agent) : null,
    live ? terminalButton(agent, { cls: "quiet" }) : null,
    openInHerdrButton(agent),
    agent ? watchCloselyButton(agent) : null,
    n.read ? null : h("button", { class: live ? "btn quiet" : "btn primary", type: "button",
      on: { click: () => markNotificationRead(n.id) }, text: UI.labels.read, "aria-label": "Mark read" }),
  ];
}

// Tier -> state hue for the dot and pill. Red only for waiting on you.
const NOTIF_HUE = { waiting_on_you: "needs_you", might_look: "caution", fyi_finished: "done",
  waiting_on_coordinator: "waiting", coordinator_milestone: "done", everything: "parked" };

function sentLine(n) {
  const d = n.delivery || {};
  const where = (d.channels || []).map((c) => ({ phone: "Phone", desktop: "Desktop" }[c] || c)).join(" and ");
  const how = d.state === "held" ? "Held back, not sent" : d.state === "sent" ? (where || "Dashboard only") : "Not sent";
  const gate = n.gate ? { held: "AI check: held", reworded: "AI check: reworded", sent: "AI check: passed" }[n.gate.decision] : null;
  return [how, gate].filter(Boolean).join(" · ");
}

function notificationCard(n, focused) {
  const who = [n.agent && n.agent.name, n.workspace && n.workspace.label].filter(Boolean).join(" · ");
  const actionable = notifActionable(n);
  const hue = actionable ? (NOTIF_HUE[n.tier] || "caution") : "parked";
  const when = n.at_label || (n.at ? clock(n.at) : "");
  const classes = ["card", "notif", n.read ? "is-read" : "is-unread", focused ? "is-focused" : ""].filter(Boolean).join(" ");
  if (!focused) {
    // Compact row: the row opens the detail. The pill and the headline come
    // first and are never cut (v0.9.4: at 390 px Reply + Open in Herdr + Read
    // left no room). A lone Read stays beside the text; two or more actions go
    // on their own line. One main button (Reply on an actionable row), the
    // rest quiet. Accent only when the row is actionable.
    const agent = agentById(n.agent?.identity);
    const open = openInHerdrButton(agent);
    open?.classList.add("quiet");
    const reply = easyReplyButton(agent,{eventId:n.id,anchor:`notification:${n.id}`,question:n.next_action?.question,primary:actionable});
    if (!actionable) reply?.classList.add("quiet");
    const actions = [
      reply,
      open,
      n.read ? null : inlineMarkRead(`notification:${n.id}`, () => markNotificationRead(n.id))].filter(Boolean);
    return h("article", {class:`${classes}${actionable ? " is-actionable" : ""}${actions.length > 1 ? " notif-stacked" : ""}`,id:`n-${n.id}`,"data-reading-key":`n:${n.id}`},
      h("a",{class:"notif-open",href:hashLink("notification",String(n.id)),"aria-label":`Open: ${n.what || "notification"}`},
        h("span",{class:"notif-what"},h("span",{class:`pill state s-${n.read ? "parked" : hue}`,text:n.tier_label || tierHead(n.tier)})," ",
          h("span",{class:"notif-headline"},mdLine(n.what || ""))),
        h("span",{class:"notif-sub"},mdLine(n.body || n.why || "", UI.ask_items.cut.line)),
        h("span",{class:"notif-meta",text:[n.agent?.name,when,n.group_count>1?`${n.group_count} updates`:null].filter(Boolean).join(" · ")})),
      actions.length ? h("div", {class:"notif-row-actions"}, actions) : null,
      replySlot(`notification:${n.id}`,agent));
  }
  return h("article", { class: classes, id: `n-${n.id}`, "data-reading-key": `n:${n.id}`, "aria-current": "true", tabindex: "-1" },
    actionLine(agentById(n.agent?.identity), n.next_action),
    h("div", { class: "notif-top-row" },
      h("span", { class: `pill state s-${hue}`, text: n.tier_label || tierHead(n.tier) }),
      h("span", { class: "notif-when", text: [when, who].filter(Boolean).join(" · ") })),
    h("h2", { class: "notif-title", text: n.what || "Notification" }),
    h("p",{class:"notif-body",text:n.body || n.why || ""}),
    h("span", { class: "sr-only", text: n.read ? "Read." : "Unread." }),
    n.evidence ? h("blockquote", { class: "notif-evidence", "data-selectable": "output", "data-agent-identity": n.agent?.identity || "" },
      mdBlock(n.evidence, { limit: UI.ask_items.cut.card, place: `evidence:${n.id}` })) : null,
    h("dl", { class: "notif-facts" },
      h("dt", { text: contentLabel("why") }), h("dd", { text: n.why || "For your information." }),
      h("dt", { text: contentLabel("action") }), h("dd", { class: "notif-do", text: n.action || "Nothing; this is for your information." }),
      h("dt", { text: "Sent" }), h("dd", { text: sentLine(n) })),
    h("details",{"data-reading-key":`raw:${n.id}`},h("summary",{text:"Technical details"}),
      h("p",{class:"muted small",text:n.technical?.what || ""}),gateLine(n.gate)),
    n.group_count > 1 ? h("details", {"data-reading-key":`updates:${n.id}`},
      h("summary", {text:`${n.group_count} updates`}),
      h("ul", {class:"plain"}, (app.notifications?.items || []).filter(item=>n.group_ids.includes(item.id) && item.id!==n.id)
        .sort((a,b)=>b.at-a.at).map(item=>h("li", {}, h("a", {href:hashLink("notification",String(item.id)),text:item.what}),
          h("span", {class:"muted small",text:` · ${item.at_label || clock(item.at)}`}))))) : null,
    h("div", { class: "chips notif-links" }, notificationLinks(n)),
    h("div", { class: "actions notif-actions" }, notificationActions(n)),replySlot(`notification:${n.id}`,agentById(n.agent?.identity)));
}

function notifActionable(n) {
  return n.actionable ?? !!tierTable().find((t) => t.id === n.tier)?.actionable;
}

function renderNotifications(r) {
  const data = app.notifications;
  const stale = !data || (now() - (data.at || 0)) * 1000 > POLL_MS;
  if (stale) loadNotifications(r.id);
  const focusId = r.id ? String(r.id) : null;
  const items = (data && data.items) || [];
  // Read and unread groups are separate in the API. Count the rows we render.
  const grouped = items.filter(n=>n.is_group_latest!==false);
  const unread = grouped.filter(n=>!n.read).length;
  const readCount = grouped.length - unread;
  const showRead = showReadNotifications();
  const readToggle = readCount ? h("button", { class: "btn quiet", type: "button", "aria-pressed": String(showRead),
    on: { click: toggleShowRead }, text: showRead ? "Hide read" : `Show read (${readCount})` }) : null;
  const gone = focusId && app.notifGone === focusId;
  const rows=items.filter(n=>n.group_ids?.map(String).includes(focusId) ? String(n.id)===focusId : n.is_group_latest!==false)
    .filter(n=>!n.read || showRead || String(n.id)===focusId)
    .sort((a,b)=>(b.group_at || b.at)-(a.group_at || a.at) || b.id-a.id);
  const actions=rows.filter(n=>!n.read && notifActionable(n)), other=rows.filter(n=>!n.read && !notifActionable(n));
  const read=rows.filter(n=>n.read);
  const cards=list=>list.map(n=>notificationCard(n,String(n.id)===focusId));
  // Needs action and Other updates follow Alex's order (Arrange); Read stays last.
  // Open questions lead Needs action; with none, the zero state is not repeated here.
  const blocks={
    needs_action:[questionEntries(app.view).length ? questionsBlock(app.view) : null,
      actions.length?h("section",{class:"notif-group","data-reading-key":"needs-action"},h("h2",{class:"attention-tier",text:`${sectionLabel("needs_action")} · ${actions.length}`}),cards(actions)):null],
    other_updates:other.length?h("details",{class:"notif-other","data-reading-key":"other-updates",open:!actions.length || other.some(n=>String(n.id)===focusId)},
      h("summary",{text:`${sectionLabel("other_updates")} (${other.length})`}),cards(other)):null,
  };
  return h("div", {class:"notifications","data-reading-key":"notifications"},
    h("div",{class:"notif-top"},h("h1",{text:"Notifications"}),arrangeButton(),
      unread?h("button",{class:"btn",type:"button",on:{click:markAllNotificationsRead},text:UI.labels.read_all}):null),
    gone?h("p",{class:"note",role:"status",text:NOTIF_GONE}):null,
    data?.error?h("p",{class:"error",text:`Could not load notifications: ${data.error}`}):null,
    readToggle,
    !data?h("p",{text:"Loading…"}):!rows.length?h("p",{class:"card",text:readCount ? `All caught up. ${readCount} read updates hidden.` : "No notifications yet. You are all caught up."}):null,
    arrangedBlocks(blocks),
    hiddenBlocksNote(blocks,{other_updates:other.length}),
    read.length?h("section",{class:"notif-group notif-read","data-reading-key":"read-updates"},
      h("h2",{class:"attention-tier",text:`Read · ${read.length}`}),cards(read)):null);

}

// Bring the focused notification into view once per focus, not on every poll.
function focusNotification(r) {
  if (!r.id || app.notifScrolled === String(r.id)) return;
  const el = document.getElementById(`n-${r.id}`);
  if (!el) return;
  app.notifScrolled = String(r.id);
  requestAnimationFrame(() => { el.scrollIntoView({ block: "start" }); el.focus({ preventScroll: true }); });
}

// ---------------------------------------------------------------- workspace

// Inside a workspace the section already names the state, so the card only
// adds a pill when it says more (which kind of "needs you", read, stall).
// The avatar's corner: the account icon when the account is recorded (v0.9.7, drawn by accountIcon),
// else the agent kind as before. One mark, never both.
function avatarCorner(a) {
  const icon = compactAccountIcon(a), mark = icon ? accountIcon(icon, { size: 16, text: false }) : null;
  if (!mark) return null;   // v0.9.7.4: the model badge next to the name says what it runs on
  mark.classList.add("avatar-account");
  return mark;
}

function agentCard(a, showState = false) {
  const task = a.title ? h("p", { class: "agent-task", text: a.title }) : h("p", { class: "agent-task none", text: "no task title" });
  const special = NEEDS.includes(a.kind) ? `${a.label}${a.acked ? " · read" : ""}` : null;
  const meta = h("div", { class: "agent-meta" },
    special || showState ? h("span", { class: `pill state s-${a.state}`, text: special || a.state_label }) : null,
    a.quota_wall ? h("span", { class: "warn", text: `${a.quota_wall.provider} quota-walled` }) : null,
    a.launch?.sandbox ? h("span", { class: "tag", text: `Sandbox: ${a.launch.sandbox}` }) : null,
    a.kind === "stall" ? h("span", { class: "warn", text: "possibly stalled" }) : null,
    a.tab_label ? h("span", { class: "tag", text: a.tab_label }) : null,
    a.state !== "done" ? h("span", { text: lastActivity(a) }) : null,
    a.last_done && a.state !== "done" ? h("span", { text: `· last done ${clock(a.last_done.at)}` }) : null);
  const card = h("a", { class: "card agent", href: hashLink('agent', a.identity) },
    h("div", { class: `avatar s-${a.state}` }, initials(a), avatarCorner(a)),
    h("div", {},
      h("div", { class: "agent-top" }, h("span", { class: "agent-name" }, displayName(a), modelBadge(a), watchBadge(a)), h("span", { class: "agent-since", text: since(a) })),
      task, meta));
  const focus = openInHerdrButton(a);
  return h("div", { class: "agent-with-focus", "data-reply-anchor":`card:${a.identity}` }, card,
    h("div",{class:"actions"},agentTier(a)==='waiting_on_you'?easyReplyButton(a,{question:a.question}):null,terminalButton(a,{cls:"quiet"}),focus),replySlot(`card:${a.identity}`,a));
}

// Reporting lines known for a workspace (either end in it), counted from
// view.links with the same declared/inferred split the graph draws.
function reportingLineNote(view, workspaceId) {
  const inWs = new Set((view.agents || []).filter((a) => a.workspace_id === workspaceId).map((a) => a.identity));
  let declared = 0, inferred = 0;
  for (const l of view.links || []) {
    if (l.kind !== "reports_to" || !(inWs.has(l.from) || inWs.has(l.to))) continue;
    if (l.inferred || l.dashed || (l.provenance || []).includes("inferred")) inferred++; else declared++;
  }
  return declared || inferred
    ? `Tabs below. Reporting lines: see graph (${declared} declared, ${inferred} inferred).`
    : "No reporting lines recorded for these agents yet.";
}

function renderWorkspace(r) {
  const view = app.view;
  const ws = view.workspaces.find((w) => w.id === r.id);
  if (!ws) {
    return h("div", {}, h("a", { class: "back", href: hashLink('list'), text: "Workspaces" }),
      h("section", { class: "card empty" }, h("h1", { text: "Not watched" }),
        h("p", { class: "muted", text: "This workspace is not watched, so nothing about it is shown." }),
        h("a", { class: "btn", href: hashLink('settings'), text: "Choose workspaces" })));
  }
  const agents = view.agents.filter((a) => a.workspace_id === ws.id && !a.folded);
  const byId = Object.fromEntries(agents.map((a) => [a.identity, a]));
  const sections = STATES.map((s) => [s, agents.filter((a) => a.state === s)]).filter(([, list]) => list.length);

  const previous = app.trail[app.trail.length - 2] || hashLink("home");
  return h("div", { "data-reading-key": `workspace:${ws.id}` },
    h("div", { class: "workspace-nav" },
      h("a", { class: "back", href: previous, text: "Back" }),
      h("a", { class: "btn", href: hashLink("home"), on: { click: () => { app.graphRegion = ws.id; } }, text: "Open in graph" }),
      cleanupButton(ws.id)),
    h("h1", { text: ws.label }),
    h("p", { class: "lede", text: `${ws.agent_count} agent${ws.agent_count === 1 ? "" : "s"}${ws.muted ? " · phone alerts muted" : ""}` }),
    ws.agent_count ? workspaceGraph(view, ws.id) : null,
    ws.agent_count ? stateBar(ws.counts) : null,
    layoutHints(ws.layout_hints),
    ws.agent_count ? legend(ws.counts) : h("p", { class: "muted", text: "No agents running here right now." }),
    visualsStrip(`workspace:${ws.id}`,`workspace=${encodeURIComponent(ws.id)}`),
    h("div", { class: "split" },
      h("div", {}, sections.map(([s, list]) => [
        h("h2", { class: "section", text: `${STATE_LABEL[s]} · ${list.length}` }),
        h("div", { class: "grid" }, list.map((a) => agentCard(a))),
      ])),
      h("aside", {},
        h("h2", { class: "section", text: "How it is arranged" }),
        h("div", { class: "layout" }, ws.tabs.map((tab) => h("div", { class: "tabrow" },
          h("span", { class: "tabname", text: tab.label + (tab.layout_hints?.length ? ` · layout: ${tab.layout_hints.length}` : "") }),
          h("div", { class: "chips" }, tab.agents.map((id) => byId[id]).filter(Boolean).map((a) =>
            h("a", { class: "chip", href: hashLink('agent', a.identity) }, h("i", { class: `dot state s-${a.state}` }), displayName(a))))))),
        h("p", { class: "note", text: reportingLineNote(view, ws.id) }))));
}

// ---------------------------------------------------------------- clean up
// Preview (made by the server) -> tick or untick -> confirm. The sheet has its
// own root, so a live refresh never rebuilds it; only its own job polling
// patches it in place. Nothing is closed until the confirm button.

function cleanupButton(workspace) {
  const labels = UI.cleanup.labels;
  return h("button", { class: "btn quiet cleanup-open", type: "button", "data-cleanup": workspace || "all",
    on: { click: () => openCleanup(workspace) }, text: workspace ? labels.open : labels.open_all });
}

function cleanupWorkspaceLabel(id) {
  return (app.view?.workspaces || []).find((w) => w.id === id)?.label || id;
}

async function openCleanup(workspace) {
  const c = app.cleanup = { workspace: workspace || null, preview: null, result: null, log: [], ticked: new Set(), error: null, busy: true };
  renderCleanup();
  try {
    const job = await api(UI.api.cleanup_preview, { method: "POST", body: workspace ? { workspace } : {} });
    const ready = await followCleanup(c, job);
    // Candidates start unticked: Alex picks each pane to close.
    if (ready) { c.preview = ready; c.ticked = new Set(); }
  } catch (err) { c.error = err.message; }
  c.busy = false;
  if (app.cleanup === c) renderCleanup();
}

// Poll one job until it is ready or failed. Null when the sheet was closed or the job failed.
async function followCleanup(c, job) {
  while (app.cleanup === c && job.status === "running") {
    await new Promise((ok) => setTimeout(ok, UI.cleanup.poll_ms));
    job = await api(`${UI.api.cleanup}?id=${job.id}`);
  }
  if (app.cleanup !== c) return null;
  if (job.status !== "ready") { c.error = job.error || "The check failed."; return null; }
  return job;
}

async function confirmCleanup() {
  const c = app.cleanup;
  const panes = (c?.preview?.candidates || []).map((row) => row.pane_id).filter((id) => c.ticked.has(id));
  if (!panes.length || c.busy) return;
  c.busy = true; c.error = null; renderCleanup();
  try {
    const job = await api(UI.api.cleanup_confirm, { method: "POST", body: { preview_id: c.preview.preview_id, panes, confirm: true } });
    const done = await followCleanup(c, job);
    if (done) {
      c.result = done;
      c.log = (await api(UI.api.cleanup)).log || [];
    } else c.preview = null;     // a preview is used once: check again
  } catch (err) { c.error = err.message; c.preview = null; }
  c.busy = false;
  if (app.cleanup === c) { renderCleanup(); refresh(true); }
}

async function allowCleanup(workspace) {
  const current = app.view?.settings?.["cleanup.workspaces"] || [];
  if (await saveSettings({ "cleanup.workspaces": [...new Set([...current, workspace])] })) openCleanup(workspace);
}

function closeCleanup() {
  app.cleanup = null;
  renderCleanup();
}

function cleanupRow(row, c) {
  const ticked = c.ticked.has(row.pane_id);
  return h("label", { class: "cleanup-row", "data-reading-key": `cleanup:${row.pane_id}` },
    h("input", { type: "checkbox", checked: ticked, disabled: c.busy, "aria-label": `Close ${row.name}`,
      on: { change: (e) => { if (e.target.checked) c.ticked.add(row.pane_id); else c.ticked.delete(row.pane_id); renderCleanup(); } } }),
    h("span", { class: "cleanup-text" },
      h("span", { class: "cleanup-name", text: row.name }),
      h("span", { class: "muted small", text: [row.pane_id, row.workspace_label, row.folder].filter(Boolean).join(" · ") }),
      h("span", { class: "small", text: row.reason })));
}

function cleanupBody(c) {
  const labels = UI.cleanup.labels;
  if (c.result) {
    const list = (title, rows, text) => rows.length ? [h("h3", { class: "sub", text: `${title} · ${rows.length}` }),
      h("ul", { class: "plain cleanup-done" }, rows.map((row) => h("li", { text: text(row) })))] : null;
    return [list(labels.closed, c.result.closed, (row) => `${row.name} (${row.pane_id})`),
      list(labels.skipped, c.result.skipped, (row) => `${row.pane_id}: ${row.reason}`),
      list(labels.failed, c.result.failed, (row) => `${row.name} (${row.pane_id}): ${row.error}`),
      list(labels.log, c.log.slice(0, 10), (row) => `${clock(row.at)} · ${row.name || row.pane_id} · ${row.result}`)];
  }
  if (!c.preview) return [c.busy ? h("p", { class: "muted", role: "status", text: labels.checking }) : null];
  const p = c.preview;
  const off = c.workspace && p.workspaces_off.find((w) => w.workspace_id === c.workspace);
  const left = [...p.excluded.map((row) => `${row.name} (${row.pane_id}): ${row.reason}`),
    ...p.workspaces_off.filter((w) => w !== off).map((w) => `${w.label}: ${w.reason} ${w.panes} pane(s).`)];
  return [
    c.busy ? h("p", { class: "muted", role: "status", text: labels.closing }) : null,
    off ? h("div", { class: "cleanup-off" }, h("p", { text: off.reason }),
      off.reason === labels.off ? h("button", { class: "btn", type: "button", on: { click: () => allowCleanup(c.workspace) }, text: labels.allow }) : null) : null,
    p.candidates.length ? h("div", { class: "cleanup-list", role: "group", "aria-label": labels.title }, p.candidates.map((row) => cleanupRow(row, c)))
      : off ? null : h("p", { class: "muted", text: labels.none }),
    left.length ? h("details", { class: "cleanup-left", "data-reading-key": "cleanup-left" },
      h("summary", { text: `${labels.excluded} (${left.length})` }),
      h("ul", { class: "plain" }, left.map((text) => h("li", { class: "small", text })))) : null];
}

function cleanupActions(c) {
  const labels = UI.cleanup.labels;
  if (c.result || (!c.preview && !c.busy)) return [
    h("button", { class: "btn", type: "button", on: { click: () => openCleanup(c.workspace) }, text: labels.again }),
    h("button", { class: "btn quiet", type: "button", on: { click: closeCleanup }, text: labels.close })];
  const n = c.preview ? c.preview.candidates.filter((row) => c.ticked.has(row.pane_id)).length : 0;
  return [
    c.preview?.candidates.length ? h("button", { class: "btn cleanup-confirm", type: "button", disabled: c.busy || !n,
      on: { click: confirmCleanup }, text: n ? labels.confirm.replace("{n}", n) : labels.pick }) : null,
    h("button", { class: "btn quiet", type: "button", disabled: c.busy && !!c.preview, on: { click: closeCleanup }, text: labels.cancel })];
}

function renderCleanup() {
  let root = $("cleanup");
  if (!root) {
    root = h("div", { id: "cleanup" });
    document.body.appendChild(root);
  }
  const c = app.cleanup;
  if (!c) { root.replaceChildren(); root.hidden = true; return; }
  root.hidden = false;
  const labels = UI.cleanup.labels;
  updateReadingView(root, h("div", { class: "modal-backdrop", "data-reading-key": "cleanup",
    on: { click: (e) => { if (e.target === e.currentTarget && !c.busy) closeCleanup(); } } },
    h("div", { class: "modal cleanup", role: "dialog", "aria-label": labels.title },
      h("h2", { text: c.workspace ? `${labels.title} · ${cleanupWorkspaceLabel(c.workspace)}` : labels.title }),
      h("p", { class: "muted small", text: labels.intro }),
      cleanupBody(c),
      c.error ? h("p", { class: "error", role: "alert", text: c.error }) : null,
      h("div", { class: "actions" }, cleanupActions(c)))));
}

// ---------------------------------------------------------------- agent

async function loadDetail(identity) {
  app.detailToken = app.detailToken || {};
  const token = (app.detailToken[identity] = (app.detailToken[identity] || 0) + 1);
  try {
    const detail = await api(`${UI.api.agent}?identity=${encodeURIComponent(identity)}`);
    if (token !== app.detailToken[identity]) return;   // an older request finished late
    app.details[identity] = detail;
  } catch (err) {
    if (err.message !== "login required") app.details[identity] = { error: err.message, generated_at: now() };
  }
  render();
}

async function loadOutput(identity, raw = false) {
  app.outputs[identity] = { loading: true };
  render();
  try {
    app.outputs[identity] = await api(`${UI.api.output}?identity=${encodeURIComponent(identity)}${raw ? "&raw=1" : ""}`);
  } catch (err) {
    app.outputs[identity] = { error: err.message };
  }
  render();
}

// Mark read closes the item: back to the list it was opened from (same
// scroll position), or out of the graph inspector. On failure it stays open
// with the error; it never pretends to be read.
async function ack(eventId, identity, {navigate=true} = {}) {
  app.ackErrors[identity] = null;
  try {
    await api(apiPath("event_ack", eventId), { method: "POST" });
  } catch (err) {
    app.ackErrors[identity] = err.message === "login required" ? "Not logged in." : err.message;
    if (!navigate) flash(`Not marked read: ${app.ackErrors[identity]}`);
    render(true);
    return;
  }
  const r = route();
  if (navigate && r.name === "graph") selectNode(null);
  else if (navigate && r.name === "agent") goBack(app.view.agents.find((a) => a.identity === identity));
  await refresh(true);
}

function goBack(agent) {
  if (app.trail.length > 1) { app.trail.pop(); history.back(); return; }
  location.hash = agent ? hashLink('workspace', agent.workspace_id) : hashLink('list');
}

const STATUS_WORDS = {
  working: "Started working", idle: "Went idle", done: "Finished a turn",
  blocked: "Waiting for you", unknown: "Status unclear",
};

function timelineItem(item) {
  const label = {
    seen: `Seen ${item.status || "with unclear status"}`,
    status: STATUS_WORDS[item.status] || `Status: ${item.status}`,
    title: "New task title",
    gone: "Gone",
    reply: item.status === "sent" ? "Reply sent" : "Reply not sent",
  }[item.kind] || item.kind;
  const tone = { working: "s-working", blocked: "s-needs_you", done: "s-done", idle: "s-waiting" }[item.status] || "";
  return h("li", { class: tone },
    h("div", { class: "when", text: `${item.live ? "" : "≈ "}${clock(item.at)}` }),
    h("div", { class: "what" }, h("b", { text: label }), item.title ? ` · ${item.title}` : ""));
}

// One note for the whole list, only when some time is approximate.
function timeline(activity, limit) {
  const items = activity.slice(0, limit);
  return [h("ul", { class: "timeline" }, items.map(timelineItem)),
    items.some((item) => !item.live) ? h("p", { class: "note", text: "≈ The collector noticed this later; the real time is unknown." }) : null];
}

// Deep links from notifications: #/a/<identity>/conversation and /output open
// the page with Details open, scrolled to "Conversation and terminal", and
// load that part once. Plain #/a/<identity> is unchanged.
const AGENT_SECTIONS = Object.keys(UI.views).filter((name) => UI.views[name] === "agent" && name !== "agent");

function openAgentSection(r) {
  if (app.sectionShown === location.hash) return;
  if (r.section === "terminal") { app.sectionShown = location.hash; return; }   // v0.9.8: the sheet (terminal.js)
  if (r.section === "report") {
    const report = $("coordinator-report"); if (!report) return;
    app.sectionShown = location.hash; setTimeout(() => report.scrollIntoView({block:"start"}),0); return;
  }
  const target = $("agent-conversation");
  const more = document.querySelector("details.more");
  if (!target || !more) return;
  app.sectionShown = location.hash;
  more.open = true;
  setTimeout(() => target.scrollIntoView({ block: "start" }), 0);
  const agent = app.view.agents.find((a) => a.identity === r.id);
  if (r.section === "conversation" && !graph.conversation[r.id]) loadConversation(r.id, 10, false);
  if (r.section === "output" && agent && agent.present && !app.outputs[r.id]) loadOutput(r.id);
}

function renderAgent(r) {
  const fromView = app.view.agents.find((a) => a.identity === r.id);
  const detail = app.details[r.id];
  if (!detail || (Date.now() / 1000 - (detail.generated_at || 0)) * 1000 > DETAIL_MS) loadDetailSoon(r.id);
  const a = fromView ? { ...detail?.agent, ...fromView } : null;
  if (!a) {
    return h("div", {}, h("a", { class: "back", href: hashLink('list'), text: "Workspaces" }),
      h("section", { class: "card empty" }, h("h1", { text: "Not available" }),
        h("p", { class: "muted", text: "This agent is gone or not in a watched workspace." })));
  }
  const completed = (detail?.events || []).filter((e) => e.type === "completion");
  const alerts = (detail?.events || []).filter((e) => e.type !== "completion");

  return h("div", { "data-reading-key": `agent:${r.id}` },
    h("a", { class: "back", href: hashLink('workspace', a.workspace_id), text: a.workspace_label }),
    h("div", { class: "detail-head" },
      h("div", { class: `avatar s-${agentTier(a) === "waiting_on_you" ? "needs_you" : a.state}`, "aria-hidden": "true" }, initials(a)),
      h("div", { class: "detail-name" },
        h("h1", { text: displayName(a) }),
        h("p", { class: "detail-sub" },
          a.title ? h("span", { class: "task", text: a.title }) : h("span", { class: "task" }, unknown("no task title reported")),
          ` · ${a.runtime?.service?.label || a.agent || "agent"}`)), watchCloselyButton(a)),
    h("p", { class: "muted small watch-explanation", text: UI.watch_closely.labels.hint }),
    agentPills(a),
    runsOn(a),
    !a.present ? h("p", { class: "note", "data-reading-key": "gone", text: "This agent is no longer running. Showing its last known details." }) : null,
    a.quota_wall ? h("p", { class: "note", "data-reading-key": "quota-wall", text: `${a.quota_wall.provider} quota-walled until ${a.quota_wall.until ? clock(a.quota_wall.until) : "cleared"}. Routing skips this provider; no automatic restart.` }) : null,
    a.launch?.sandbox ? h("p", { class: "note", "data-reading-key": "sandbox", text: `Sandbox: ${a.launch.sandbox}` }) : null,
    watchedQuestions(a),
    summaryCard(a, detail),
    subagentFold(a),
    coordinatorReport(a),
    // The coordinator report already includes the team strip; a second Latest visuals repeats it.
    a.is_coordinator ? null : visualsStrip(`agent:${a.identity}`,`identity=${encodeURIComponent(a.identity)}`),
    agentStats(a),
    agentGoTo(a),
    h("section", { class: "card recent-timeline", "data-reading-key": "recent-timeline" },
      h("h2", { class: "sub", text: "Recent history" }),
      detail?.activity?.length ? timeline(detail.activity, 3) : h("p", { class: "muted small", text: "Nothing recorded yet." }),
      h("button", { class: "btn quiet", type: "button", text: "Full history", on: { click: () => {
        const more = document.querySelector("details.more"); if (more) more.open = true;
        document.querySelector("#agent-history")?.scrollIntoView({ block: "start" });
      } } })),
    h("details", { class: "more", "data-reading-key": "agent-facts" }, h("summary", { text: "Details: facts, metrics, evidence, history" }),
    h("div", { class: "split" },
      h("div", {},
        h("section", { class: "card" },
          h("dl", { class: "facts" },
            h("dt", { text: "In this state" }), h("dd", { text: `${since(a)}${a.since_known ? "" : " (started before the collector saw it)"}` }),
            h("dt", { text: "Last activity" }), h("dd", { text: lastActivity(a) }),
            h("dt", { text: "Last seen" }), h("dd", { text: !a.present ? "gone" : app.view.collector.herdr.connected ? "here now" : `${ago(a.last_seen)} ago` }),
            h("dt", { text: "Tab" }), h("dd", {}, a.tab_label || unknown()),
            h("dt", { text: "Folder" }), h("dd", {}, a.cwd || unknown()),
            h("dt", { text: "Agent" }), h("dd", { text: a.agent || "unknown" }),
            h("dt", { text: "Reports to" }), h("dd", {}, a.reports_to
              ? (a.reports_to.identity ? h("a", { class: "chip", href: hashLink('agent', a.reports_to.identity), text: a.reports_to.name }) : a.reports_to.name)
              : unknown("none declared")),
            h("dt", { text: "Last finished" }), h("dd", {}, a.last_done ? `${clock(a.last_done.at)}${a.last_done.title ? ` · ${a.last_done.title}` : ""}` : unknown("nothing seen yet"))),
          h("div", { class: "actions" },
            h("a", { class: "btn", href: hashLink('graph', a.identity), text: "Show in graph" }))),
        h("h2", { class: "section", text: "Metrics" }), metricsCard(a),
        h("h2", { class: "section", text: "Connections" }), connectionsCard(a, app.view),
        h("h2", { class: "section", id: "agent-conversation", text: "Conversation and terminal" }), conversationCard(a),
        h("h2", { class: "section", text: "Why this state" }),
        h("section", { class: "card" }, h("ul", { class: "evidence" }, evidenceLines(a).map((line) => evidenceItem(line, a)))),
        h("h2", { class: "section", text: "Finished work" }),
        h("section", { class: "card" },
          completed.length ? h("ul", { class: "timeline" }, completed.map((e) => h("li", { class: "s-done" },
            h("div", { class: "when", text: `${clock(e.at)}${e.state_label ? ` · ${e.state_label}` : ""}${e.seen_late ? " · seen late" : ""}` }),
            h("div", { class: "what" }, e.title || unknown("no title at the time")))))
            : h("p", { class: "muted small", text: detail ? "No finished turns seen in the last days." : "Loading…" })),
        alerts.length ? [h("h2", { class: "section", text: "Recent alerts" }),
          h("section", { class: "card" }, h("ul", { class: "timeline" }, alerts.slice(0, 8).map((e) => h("li", { class: "s-needs_you" },
            h("div", { class: "when", text: `${clock(e.at)}${e.resolved_at ? ` · over at ${clock(e.resolved_at)}` : " · open"}` }),
            h("div", { class: "what", text: e.label })))))] : null),
      h("aside", {},
        h("h2", { class: "section", id: "agent-history", text: "Timeline" }),
        h("section", { class: "card" },
          detail?.activity?.length ? timeline(detail.activity, 30)
            : h("p", { class: "muted small", text: detail ? "Nothing recorded yet." : "Loading…" })),
        detail?.same_tab?.length ? [h("h2", { class: "section", text: "Same tab" }),
          h("div", { class: "chips" }, detail.same_tab.map((p) => h("a", { class: "chip", href: hashLink('agent', p.identity), text: p.name || p.agent }))),
          h("p", { class: "note", text: reportingLineNote(app.view, a.workspace_id) })] : null))));
}

// Media discovery runs only when this section is opened; no poll starts a scan.
const VISUALS_WAIT_MS = 20000;
const VISUALS_SLOW = "The search for images took too long. Open Latest visuals on one agent instead.";
async function loadVisuals(key, query, force = false) {
  app.visuals=app.visuals || {}; app.visuals[key]={...(app.visuals[key] || {}),loading:true,open:true};
  render(true);
  // A big workspace can take long to scan; stop waiting after VISUALS_WAIT_MS and say what to do.
  try { app.visuals[key]={...await api(`${UI.api.visuals}?${query}${force ? "&refresh=1" : ""}`,{signal:AbortSignal.timeout(VISUALS_WAIT_MS)}),open:true}; }
  catch (err) {app.visuals[key]={error:err.name==="TimeoutError"||err.name==="AbortError" ? VISUALS_SLOW : err.message,open:true};}
  render(true);
}

function visualsStrip(key, query) {
  const data=app.visuals?.[key];
  return h("details",{class:"card latest-visuals","data-reading-key":`visuals:${key}`,open:!!data?.open,on:{toggle:event=>{
    if (!event.target.isConnected) return;
    if (event.target.open && !app.visuals?.[key]) loadVisuals(key,query);
    else if (app.visuals?.[key]) app.visuals[key].open=event.target.open;
  }}},h("summary",{text:"Latest visuals"}),
    data ? [h("div",{class:"actions"},h("button",{class:"btn quiet",type:"button",disabled:data.loading,text:data.loading ? "Looking…" : "Refresh visuals",on:{click:()=>loadVisuals(key,query,true)}}),
      h("a",{class:"btn quiet",href:hashLink("visuals"),text:"All visuals"})),
      data.error ? h("p",{class:"error",text:data.error}) : null,
      data.partial ? h("p",{class:"muted small visuals-partial",text:`Not all folders were scanned${data.partial_reason ? `: ${data.partial_reason}` : ""}.`}) : null,
      data.items?.length ? h("div",{class:"visual-strip"},data.items.map(item=>h("button",{class:"visual-tile",type:"button",on:{click:()=>openVisual(item)}},
        item.video ? h("span",{class:"video-preview"},visualThumb(item, "Video preview"),h("span",{text:"Play video"})) : visualThumb(item, item.name),
        h("span",{class:"visual-name",text:item.name}),h("span",{class:"muted small",text:`${item.source==='trace' ? item.agent : 'Workspace file'} · ${ago(item.at)} ago` })))) :
        !data.loading ? h("p",{class:"muted",text:"No recent images or videos found in this watched folder."})
        : h("p",{class:"muted small",role:"status",text:"Looking for images and videos from the last 48 hours…"})] : null);
}

function draftCoordinator(identity, text, steer=false) {
  openReply(identity,undefined,null,null,{steer}); app.composer.text = text; renderComposer(); scheduleReplyPreview();
}

function statusReportButton(a, {named=false}={}) {
  if (!a?.is_coordinator || app.view.summaries?.enabled === false) return null;
  return h("button", {class:"btn status-report-button",type:"button",text:named?`Status report: ${displayName(a)}`:"Status report",on:{click:async event=>{
    event.preventDefault(); event.stopPropagation();
    location.hash=hashLink("report",a.identity);
    await requestSummary(a.identity,"report");
  }}});
}

function coordinatorReport(a) {
  if (!a.is_coordinator) return null;
  const row=a.status_report, report=row?.summary, labels=UI.watch_closely.labels;
  // Current questions and blockers always come from the current snapshot.
  const ids=new Set(report?.members || [a.identity]);
  const questions=(app.view.questions || []).filter(q=>ids.has(q.identity) && !q.keep_open);
  return h("section",{class:"card coordinator-report",id:"coordinator-report","data-reading-key":"coordinator-report"},
    h("div",{class:"reply-row-head"},h("h2",{text:"Coordinator status"}),easyReplyButton(a,{anchor:`report:${a.identity}`})),
    replySlot(`report:${a.identity}`,a),statusReportButton(a),
    !row ? h("p",{class:"muted",text:"Build a report from this team’s traces and output when you need it."}) : null,
    row && row.status !== "complete" ? h("p",{text:row.status === "failed" ? `Report failed: ${row.error}` : "Building report…"}) : null,
    row?.refresh_status ? h("p",{class:"muted small report-refresh",text:row.refresh_status==='failed' ? `${labels.refresh_failed}: ${row.refresh_error || ""}` : labels.refreshing}) : null,
    report ? [h("p",{class:"report-status"},fileNodes(report.status)),
      h("p",{class:"muted small",text:`${labels.updated} ${clock(row.finished_at)} · ${row.out_of_date ? labels.out_of_date : "Cached report"}`}),
      questionEntries({...app.view,questions,agents:app.view.agents.filter(x=>ids.has(x.identity))}).map(questionEntryCard),
      [ ["Done","done"], ["In progress","in_progress"], ["What’s left","left"] ].map(([title,key])=>report[key]?.length ? h("div",{},h("h3",{text:title}),h("ul",{},report[key].slice(0,3).map(t=>h("li",{},fileNodes(t))))) : null),
      report.stuck?.length ? h("div",{},h("h3",{text:"Stuck workers"}),report.stuck.map(worker=>h("p",{},
        h("a",{href:hashLink("agent", worker.identity),text:worker.name}),` · ${worker.reason}`,
        h("button",{class:"btn quiet",type:"button",text:"Nudge coordinator",on:{click:()=>draftCoordinator(a.identity,`Please check on ${worker.name}: ${worker.reason}. What would help them continue?`)}})))) : null
    ] : null,
    h("div",{class:"actions"},h("button",{class:"btn",type:"button",text:"Steer",on:{click:()=>draftCoordinator(a.identity,"Please focus the team on: ",true)}}),
      h("button",{class:"btn quiet",type:"button",text:"Ask coordinator for status",on:{click:()=>draftCoordinator(a.identity,"Please give a short team status: done, in progress, what is left, and any blockers that need me.")}})),
    visualsStrip(`team:${a.identity}`,`identity=${encodeURIComponent(a.identity)}&team=1`),
    h("p",{class:"muted small",text:"The report uses your summary settings. Messages open as editable drafts for confirmation."}));
}

function summarizeLabel(a, failed) {
  if (app.summarizing && app.summarizing[a.identity]) return "Summarizing…";
  if (failed || (a.summary && a.summary.status === "failed")) return "Retry";
  return "Summarize";
}

function summarizeButton(a, extra = {}) {
  const cfg = (app.view && app.view.summaries) || {};
  if (cfg.enabled === false) return null;
  const busy = !!(app.summarizing && app.summarizing[a.identity]);
  return h("button", { class: "btn btn-summarize " + (extra.class || ""), type: "button", disabled: busy,
    "aria-busy": busy ? "true" : "false",
    on: { click: (event) => { event.preventDefault(); event.stopPropagation(); requestSummary(a.identity, "refresh"); } },
    "aria-label": summarizeLabel(a, extra.failed) }, icon("spark", "btn-icon"), summarizeLabel(a, extra.failed));
}

async function requestSummary(identity, mode) {
  app.summarizing = app.summarizing || {};
  app.summarizing[identity] = true;
  render(true);
  try {
    await api(UI.api.summaries, { method: "POST", body: { identity, mode } });
    flash(mode === "report" ? "Status report queued." : mode === "longer" ? "Longer summary queued." : "Summary queued.");
  } catch (err) {
    flash(`Could not queue: ${err.message}`);
  }
  delete app.summarizing[identity];
  delete app.details[identity];
  await refresh(true);
}

// Summary first: what happened and what is needed, labelled as AI, with its
// source range and freshness. Falls back to the agent's own last message.
// Theme v0.8 agent page: tier pill (solid only for Waiting on you) plus the
// specific kind; a three-number strip; "go to" chips.
function agentPills(a) {
  const you = agentTier(a) === "waiting_on_you";
  const kind = NEEDS.includes(a.kind) || a.kind === "stall" || a.awaiting_user ? (a.label || null) : null;
  return h("div", { class: "detail-pills" },
    you ? h("span", { class: "pill state s-needs_you", text: tierHead("waiting_on_you") }) : null,
    h("span", { class: `pill state s-${a.kind === "stall" ? "caution" : you ? "parked" : a.state}`,
      text: kind && kind !== tierHead("waiting_on_you") ? kind : a.state_label }));
}

function agentStats(a) {
  const reports = a.reports_to
    ? (a.reports_to.identity ? h("a", { href: hashLink('agent', a.reports_to.identity), text: a.reports_to.name }) : a.reports_to.name)
    : unknown("none");
  return h("section", { class: "card stat-row" },
    h("div", {}, h("span", { class: "stat-label", text: "In this state" }), h("b", { text: since(a) })),
    h("div", {}, h("span", { class: "stat-label", text: "Last seen" }),
      h("b", { text: !a.present ? "gone" : app.view.collector.herdr.connected ? "now" : `${ago(a.last_seen)} ago` })),
    h("div", {}, h("span", { class: "stat-label", text: "Reports to" }), h("b", {}, reports)));
}

function agentGoTo(a) {
  return h("div", { class: "chips agent-goto" }, easyReplyButton(a,{question:a.question,anchor:`agent:${a.identity}`}),replySlot(`agent:${a.identity}`,a),terminalButton(a),openInHerdrButton(a),
    h("a", { class: "chip", href: hashLink("conversation", a.identity), text: "Conversation" }),
    h("a", { class: "chip", href: hashLink("output", a.identity), text: "Terminal output" }),
    h("a", { class: "chip", href: hashLink('graph', a.identity), text: "Show in graph" }));
}

function contentLabel(key) { return app.view.content_labels?.[key] || key; }

function actionLine(a, action) {
  // v0.9.6: question items, "may want something" or "Done, not reviewed" replace the dead end.
  if (asksShown(a))
    return h("div",{class:"what-to-do","data-reading-key":"what-to-do"},asksSection(a));
  const q=action?.question;
  const key=`action:${a?.identity || action?.target}`;
  return h("div",{class:"what-to-do","data-reading-key":"what-to-do"},
    h("h3",{class:"sub",text:contentLabel("action")}),
    q?h("p",{class:"muted small",text:q.reason}):null,
    h(q?"blockquote":"p",{class:q?"evidence-quote":null},mdLine(action?.text || "Nothing to do — for your information",0,true)),
    action?.kind==="reply"?easyReplyButton(a || agentById(action.target),{question:q,anchor:key}):
    action?.kind==="open_agent"?h("a",{class:"btn primary",href:hashLink("output",action.target),text:"Open agent"}):
    action?.kind==="terminal"?easyReplyButton(a,{question:q,anchor:key}):null,replySlot(key,a));
}

function summaryCard(a, detail) {
  const row = detail && detail.summary;
  const compact = a.summary;
  const status = row ? row.status : compact ? compact.status : "none";
  const s = row && row.status === "complete" ? row.summary : null;
  const action=detail?.presentation || a.presentation;
  const stale = !!action?.out_of_date;
  const head = asksShown(a) && !s && status === "none" ? null : h("div", { class: "summary-head" },
    h("span", { class: "ai-tag", text: "AI summary" }),
    h("span", { class: `sum-status ${status}`, text: { complete: stale ? "Out of date" : "complete", queued: "queued", running: "summarizing…",
      failed: "failed", none: "none yet", skipped: "nothing to summarize" }[status] || status }),
    null);
  const metadata = row && row.status === "complete" ? h("details",{class:"summary-provenance","data-reading-key":"summary-provenance"},
    h("summary",{text:"Summary details"}),h("p",{class:"metric-meta",text:`${row.resolved_model || row.model} · effort ${row.effort} · ${clock(row.finished_at)}${row.cached_from ? " · reused (same input)" : ""}`})) : null;
  const body = [];
  if (s) {
    if (s.headline) body.push(h("p", { class: "sum-headline" }, mdLine(s.headline, 0, true)));
    const n = s.needs_you || {};
    if (!stale && a.question && n.kind && n.kind !== "none") {
      body.push(h("div", { class: "sum-needs" },
        h("b", { text: `${n.kind[0].toUpperCase()}${n.kind.slice(1)}: ` }), n.ask || "",
        n.options && n.options.length ? h("ul", { class: "options" }, n.options.map((o) => h("li", {}, h("q", { text: o })))) : null,
        n.why ? h("p", { class: "muted small", text: `Why you: ${n.why}` }) : null));
    }
    if (s.accomplished && s.accomplished.length)
      body.push(h("div", {}, h("h3", { class: "sub", text: contentLabel("done") }), h("ul", {}, s.accomplished.slice(0,3).map((x) => h("li", {}, mdLine(x.replace(/(?:\/?[\w.-]+\/)+[\w.-]+/g,"the file")))))));
    if (s.tests && s.tests.length)
      body.push(h("div", {}, h("h3", { class: "sub", text: "Tests" }), h("ul", {}, s.tests.map((t) => h("li", {},
        `${t.what}: ${t.outcome}`, h("div", { class: "metric-meta", text: `${t.status} · "${t.evidence}"` }))))));
    if (s.claims && s.claims.length)
      body.push(h("div", {}, h("h3", { class: "sub", text: "Claims" }), h("ul", {}, s.claims.map((c) => h("li", {},
        c.text, h("div", { class: "metric-meta", text: `${c.status} · "${c.evidence}"` }))))));
    if (s.why_it_matters) body.unshift(h("p",{class:"summary-why"},h("b",{text:contentLabel("why")+": "}),s.why_it_matters));
    if (s.next_step && !stale) body.push(h("p", {}, h("b", { text: "Next: " }), s.next_step));
    if (s.dropped && s.dropped.length)
      body.push(h("p", { class: "note", text: `${s.dropped.length} statement(s) left out because the source does not contain them.` }));
  } else if (asksShown(a)) {
    // The asks carry the agent's message (Show original); no "none yet" placeholder.
    if (status === "failed") body.push(h("p", { class: "error", text: `Summary failed: ${row ? row.error : compact.error}` }));
  } else {
    if (status === "failed") body.push(h("p", { class: "error", text: `Summary failed: ${row ? row.error : compact.error}` }));
    const fb = detail && detail.fallback;
    if (fb && fb.available) {
      body.push(h("p", { class: "muted small", text: `Not an AI summary: the agent's last message (${clock(fb.at)}):` }));
      body.push(mdBlock(fb.text, { limit: UI.ask_items.cut.card, place: `fallback:${a.identity}`, cls: "fallback" }));
    } else {
      body.push(h("p", { class: "muted small", text: detail ? `${a.label}. No message from the agent to show yet.` : "Loading…" }));
    }
  }
  const err = app.ackErrors[a.identity];
  return h("section", { class: "card summary", "data-reading-key": "summary", "data-selectable": "summary", "data-agent-identity": a.identity }, actionLine(a,action), head, body,
    stale ? h("button",{class:"btn",type:"button",text:"Refresh summary",on:{click:()=>requestSummary(a.identity,"refresh")}}):null,
    summaryActions(a, status), a.read_action?.reason ? h("p",{class:"muted small read-state-hint",text:a.read_action.reason}):null, metadata,
    err ? h("p", { class: "error", role: "alert", text: `Not marked read: ${err}` }) : null);
}

// One primary per card: Reply when the agent waits on Alex, else Mark read,
// else Summarize.
function summaryActions(a, status) {
  const canAck = canMarkAgentRead(a);
  const canReply = a.present && !atQuestionScreen(a) && app.view.features?.reply;
  const primary = canReply && agentTier(a) === "waiting_on_you" ? "reply" : canAck ? "ack" : "summary";
  return h("div", { class: "actions" },
    ["reply","terminal"].includes(a.presentation?.kind) ? null : replyButton(a, primary === "reply"),
    canAck ? h("button", { class: primary === "ack" && !["reply","terminal","open_agent"].includes(a.presentation?.kind) ? "btn primary" : "btn", type: "button", on: { click: () => ack(a.event_id, a.identity) }, text: "Mark read" }) : null,
    h("details", { class: "summary-menu", "data-reading-key": "summary-menu" },
      h("summary", { class: primary === "summary" && !["reply","terminal","open_agent"].includes(a.presentation?.kind) ? "btn primary" : "btn", text: "Summary" }),
      h("div", { class: "summary-options" },
        summarizeButton(a, { failed: status === "failed" }),
        h("button", { class: "btn quiet", type: "button", on: { click: () => requestSummary(a.identity, "longer") }, text: "Summarize longer" }),
        h("button", { class: "btn quiet", type: "button", on: { click: () => openAsk(a.identity) }, text: "Ask summary agent" }))));
}

function loadDetailSoon(identity) {
  if (loadDetailSoon.pending === identity) return;
  loadDetailSoon.pending = identity;
  setTimeout(() => { loadDetailSoon.pending = null; loadDetail(identity); }, 0);
}

// ---------------------------------------------------------------- settings

function toggle(checked, label, onChange, key = null) {
  const input = h("input", { type: "checkbox", role: "switch", "aria-label": label, "data-setting": key,
    on: { change: (e) => onChange(e.target.checked) } });
  input.checked = checked;
  return h("label", { class: "switch" }, input, h("span", {}));
}

// A switch that is always on: shown on, dimmed, and not clickable.
function lockedToggle(label) {
  const input = h("input", { type: "checkbox", role: "switch", "aria-label": `${label} (always on)`, disabled: true });
  input.checked = true;
  return h("label", { class: "switch locked", title: "Always on" }, input, h("span", {}));
}

// v0.9.8.3: one row per workspace. Off / Watch (shown here, no phone alerts) / Alerts
// (shown here and phone alerts). Saves the same two settings as before:
// watch.enabled_workspaces and watch.muted_workspaces (Watch = watched and muted).
const WS_MODES = [
  ["off", "Off", (l) => `${l}: off`, "Not watched: not shown here, no alerts"],
  ["watch", "Watch", (l) => `Watch ${l}`, "Shown here, no phone alerts"],
  ["alerts", "Alerts", (l) => `Watch ${l} with phone alerts`, "Shown here, with phone alerts"],
];
const wsMode = (w) => (!w.watched ? "off" : w.muted ? "watch" : "alerts");

function setWorkspaceMode(w, mode) {
  const enabled = new Set(app.chooser.filter((x) => x.watched).map((x) => x.id));
  const muted = new Set(app.chooser.filter((x) => x.muted).map((x) => x.id));
  const body = {};
  const on = mode !== "off";
  if (on !== enabled.has(w.id)) {
    if (on) enabled.add(w.id); else enabled.delete(w.id);
    body["watch.enabled_workspaces"] = [...enabled].sort();
  }
  // Off keeps the saved mute; turning on sets it from the chosen mode.
  if (on && (mode === "watch") !== muted.has(w.id)) {
    if (mode === "watch") muted.add(w.id); else muted.delete(w.id);
    body["watch.muted_workspaces"] = [...muted].sort();
  }
  if (Object.keys(body).length) saveSettings(body);
}

function workspaceRow(w) {
  const mode = wsMode(w);
  const info = (app.view?.workspaces || []).find((x) => x.id === w.id);
  const meta = !w.watched ? "Not watched" : [
    info ? `${info.agent_count} agent${info.agent_count === 1 ? "" : "s"}` : null,
    info?.last_activity_at ? `active ${ago(info.last_activity_at)} ago` : null,
    mode === "watch" ? "no phone alerts" : null,
  ].filter(Boolean).join(" · ");
  return h("div", { class: `ws-row${w.watched ? "" : " is-off"}`, "data-workspace": w.id, "data-label": w.label.toLowerCase(),
                    "data-reading-key": `ws-row:${w.id}` },
    h("div", { class: "ws-name" }, h("b", { text: w.label }), h("small", { text: meta })),
    h("div", { class: "seg", role: "radiogroup", "aria-label": `${w.label}: watch and alerts` },
      WS_MODES.map(([id, label, aria, title]) => h("button", {
        type: "button", class: `seg-btn${mode === id ? " is-on" : ""}`, role: "radio", "aria-checked": String(mode === id),
        "aria-label": aria(w.label), title, "data-mode": id, text: label,
        on: { click: () => { if (mode !== id) setWorkspaceMode(w, id); } } }))));
}

async function refreshWorkspaces() {
  if (app.wsRefresh?.busy) return;
  app.wsRefresh = { busy: true };
  render(true);
  try {
    const r = await api(UI.api.workspaces_refresh, { method: "POST", body: {} });
    if (r.ok) {
      app.chooser = r.workspaces;
      const fresh = (r.new || []).map((w) => w.label);
      app.wsRefresh = { text: `Found ${r.count} workspace${r.count === 1 ? "" : "s"}, ${fresh.length ? `${fresh.length} new: ${fresh.join(", ")}` : "none new"}.` };
    } else {
      app.wsRefresh = { text: r.detail || "Herdr did not answer.", error: true };
    }
    await refresh(true);
  } catch (err) {
    app.wsRefresh = { text: err.message, error: true };
  }
  render(true);
}

function refreshWorkspacesButton() {
  const busy = !!app.wsRefresh?.busy;
  return h("button", { type: "button", class: `btn quiet ws-refresh${busy ? " is-busy" : ""}`, disabled: busy,
                       title: "Ask Herdr for its workspaces now", "aria-label": "Refresh workspaces", on: { click: refreshWorkspaces } },
    h("span", { class: "ws-refresh-icon", "aria-hidden": "true", text: "↻" }), h("span", { text: busy ? "Refreshing…" : "Refresh" }));
}

function chooserCard() {
  if (!app.chooser) {
    api(UI.api.chooser).then((data) => { app.chooser = data.workspaces; render(true); }).catch((err) => flash(err.message));
    return h("section", { class: "card" }, h("p", { class: "muted", text: "Loading workspaces…" }));
  }
  const status = app.wsRefresh?.text ? h("p", { class: `ws-refresh-status small${app.wsRefresh.error ? " is-error" : " muted"}`, role: "status", text: app.wsRefresh.text }) : null;
  if (!app.chooser.length) return h("section", { class: "card ws-list" }, status, h("p", { class: "muted", text: "Herdr reports no workspaces." }));
  const watched = app.chooser.filter((w) => w.watched), off = app.chooser.filter((w) => !w.watched);
  const filter = app.chooser.length > 8 ? h("input", { type: "search", class: "ws-filter", placeholder: "Filter workspaces", "aria-label": "Filter workspaces",
    "data-reading-key": "ws-filter", on: { input: (e) => {
      const q = e.target.value.trim().toLowerCase();
      e.target.closest(".ws-list").querySelectorAll(".ws-row").forEach((row) => { row.hidden = !!q && !row.dataset.label.includes(q); });
    } } }) : null;
  return h("section", { class: "card ws-list", "data-reading-key": "ws-list" }, status, filter,
    h("p", { class: "ws-legend muted small", text: "Watch: shown here. Alerts: shown here and sent to your phone." }),
    watched.length ? h("div", { class: "ws-group", "aria-label": "Watched" }, watched.map(workspaceRow)) : null,
    off.length ? h("div", { class: "ws-group ws-group-off" }, h("h3", { class: "ws-group-title", text: `Not watched · ${off.length}` }), off.map(workspaceRow)) : null);
}

function stepper(value, unit, step, low, high, onChange, label = "") {
  const out = h("output", { text: `${value} ${unit}` });
  const set = (delta) => onChange(Math.max(low, Math.min(high, Number(value) + delta)));
  return h("div", { class: "stepper" },
    h("button", { type: "button", "aria-label": `${label} ${step} ${unit} less`.trim(), on: { click: () => set(-step) }, text: "−" }), out,
    h("button", { type: "button", "aria-label": `${label} ${step} ${unit} more`.trim(), on: { click: () => set(step) }, text: "+" }));
}

function settingSelect(key, value, options, onChange, label) {
  const sel = h("select", { "data-setting": key, "aria-label": label || key,
    on: { change: (e) => onChange(e.target.value) } });
  for (const opt of options) {
    const id = typeof opt === "string" ? opt : opt.id;
    const label = typeof opt === "string" ? opt : opt.label;
    const node = h("option", { value: id, text: label });
    if (String(id) === String(value)) node.selected = true;
    sel.appendChild(node);
  }
  return sel;
}

async function saveSummaryMode(mode) {
  app.audit = null;
  await saveSettings({ "summaries.enabled": mode !== "off", "summaries.auto": mode === "auto" });
}

function accountOptions(cfg) {
  return ((cfg && cfg.accounts) || []).map((a) => ({
    id: a.id,
    label: a.label + (a.available ? "" : " (not on this laptop)"),
  }));
}

function modelOptions(cfg, accountId) {
  const account = ((cfg && cfg.accounts) || []).find((a) => a.id === accountId);
  return (account && account.models) ? account.models : [];
}

const EFFORT_OPTIONS = [{ id: "low", label: "Low" }, { id: "medium", label: "Medium" }, { id: "high", label: "High" }];

function summariesCard(s) {
  if (!app.audit) api(UI.api.summaries_audit).then((data) => { app.audit = data; render(true); }).catch(() => {});
  const a = app.audit;
  const cfg = (app.view && app.view.summaries) || {};
  const mode = s["summaries.enabled"] === false ? "off" : (s["summaries.auto"] ? "auto" : "ask");
  const accountId = s["summaries.account"] || cfg.account || "codex-personal";
  return h("section", { class: "card" },
    h("div", { class: "setting stack" },
      h("div", { class: "label" }, "AI summaries",
        h("small", { text: UI.watch_closely.labels.hint })),
      settingSelect("summaries.mode", mode, [
        { id: "ask", label: "Only when I ask (default)" },
        { id: "auto", label: "Also automatically when an agent finishes" },
        { id: "off", label: "Off — never call a model" },
      ], saveSummaryMode, "AI summaries")),
    h("p", { class: "note", text: "Automatic summaries use tokens even when you do not open the dashboard." }),
    h("div", { class: "setting" }, h("div", { class: "label" }, "Account"),
      settingSelect("summaries.account", accountId, accountOptions(cfg), (v) => {
        const models = modelOptions(cfg, v);
        const current = s["summaries.model"];
        const ok = models.some((m) => m.id === current);
        const body = { "summaries.account": v };
        if (!ok && models[0]) body["summaries.model"] = models[0].id;
        saveSettings(body);
      }, "Summary account")),
    h("div", { class: "setting" }, h("div", { class: "label" }, "Model"),
      settingSelect("summaries.model", s["summaries.model"] || cfg.model, modelOptions(cfg, accountId),
        (v) => saveSetting("summaries.model", v), "Summary model")),
    h("div", { class: "setting" }, h("div", { class: "label" }, "Effort"),
      settingSelect("summaries.effort", s["summaries.effort"] || cfg.effort, EFFORT_OPTIONS,
        (v) => saveSetting("summaries.effort", v), "Summary effort")),
    a && a.recent && a.recent.length ? h("details", {}, h("summary", { text: "Recent summary runs" }),
      h("ul", { class: "plain small" }, a.recent.slice(0, 10).map((r) => h("li", {},
        `${clock(r.requested_at)} · ${r.agent} · ${r.status}${r.cached_from ? " (reused)" : ""}`,
        h("div", { class: "metric-meta", text: `${r.resolved_model || r.model} · in ${r.input_chars ?? "?"} chars · out ${r.output_chars ?? "?"} chars${r.usage ? ` · ${r.usage.input_tokens + (r.usage.cache_read_input_tokens || 0) + (r.usage.cache_creation_input_tokens || 0)} in / ${r.usage.output_tokens} out tokens` : ""}${r.error ? ` · ${r.error}` : ""}` }))))) : null);
}

function notifyTiersCard(s) {
  const cfg = (app.view && app.view.notify_tiers) || {};
  const preset = s["notify_tiers.preset"] || cfg.preset || "normal";
  // Preset labels and sentences come from notify_tiers.py; Custom only shows while it applies.
  const presets = (cfg.presets || []).filter((p) => p.id !== "custom" || preset === "custom");
  const presetHint = cfg.preset_hint || (presets.find((p) => p.id === preset) || {}).hint || "";
  const estimate = cfg.estimate || {};
  const deliveries = cfg.deliveries || [
    { id: "ntfy", label: "Phone (ntfy)" }, { id: "desktop", label: "Desktop" }, { id: "dashboard", label: "Dashboard only" },
  ];
  const rows = (cfg.tiers || []).map((tier) => {
    const on = s[`notify_tiers.${tier.id}`];
    const enabled = tier.locked ? true : (on === undefined ? !!tier.enabled : !!on);
    const delivery = s[`notify_tiers.${tier.id}_delivery`] || tier.delivery;
    return h("div", { class: "tier-row", "data-setting": `notify_tiers.${tier.id}` },
      h("div", { class: "setting" },
        h("div", { class: "label" }, tier.label, h("small", { text: tier.hint })),
        tier.locked ? lockedToggle(tier.label)
          : toggle(enabled, tier.label, (v) => saveSetting(`notify_tiers.${tier.id}`, v))),
      h("div", { class: "tier-options" },
        h("label", {}, "Send to ",
          settingSelect(`notify_tiers.${tier.id}_delivery`, delivery, deliveries,
            (v) => saveSetting(`notify_tiers.${tier.id}_delivery`, v), `${tier.label}: send to`))));
  });
  return h("section", { class: "card" },
    h("div", { class: "setting stack" },
      h("div", { class: "label" }, "How closely to watch",
        h("small", { text: "A preset sets the switches below. Changing a switch makes it Custom." })),
      h("div", { class: "segmented", role: "group", "aria-label": "Notification preset", "data-setting": "notify_tiers.preset" },
        presets.map((p) => h("button", { type: "button", "aria-pressed": String(preset === p.id),
          on: { click: () => { if (p.id !== "custom") saveSetting("notify_tiers.preset", p.id); } },
          text: p.label })))),
    h("p", { class: "note preset-hint" },
      h("b", { text: `${cfg.preset_label || preset}: ` }), presetHint,
      estimate.alert_count !== undefined ? h("span", { class: "muted small preset-estimate",
        text: ` ${estimate.capped ? "At least" : "About"} ${plural(estimate.alert_count, "alert")} in the last ${estimate.days} days.` }) : null),
    ...rows,
    h("div", {class:"setting"}, h("label", {class:"label",text:"Hide old updates from closed agents after (hours)"}),
      h("input", {type:"number",min:"1",max:"8760",value:s["notifications.stale_hours"] ?? 24,"aria-label":"Old updates age in hours","data-setting":"notifications.stale_hours",
        on:{change:e=>saveSetting("notifications.stale_hours",Number(e.target.value))}})),
    h("p", { class: "note", text: "Quiet hours, loudness and the rate limit are under Phone alerts above." }));
}

function gateHealthLine() {
  const gate=app.view?.notify_gate || {}, health=gate.health || {};
  return h("p", {class:"muted small gate-health",text:`AI notification check · ${gate.account_label || gate.account || "Account not recorded"}: ${health.state || "No checks yet"}${health.since ? ` since ${clock(health.since)}` : ""}`});
}

function notifyGateCard(s) {
  const cfg = (app.view && app.view.notify_gate) || {};
  const accountId = s["notify_gate.account"] || cfg.account || "codex-personal";
  const recent = cfg.recent || [];
  return h("div", { class: "gate-controls" },
    h("div", { class: "setting", "data-setting": "notify_gate.enabled" },
      h("div", { class: "label" }, "Check notifications before sending",
        h("small", { text: "On by default. Uses a small amount of tokens each time an alert would be sent." })),
      toggle(s["notify_gate.enabled"] !== false, "Notification gate", (on) => saveSetting("notify_gate.enabled", on), "notify_gate.enabled")),
    h("div", { class: "setting" }, h("div", { class: "label" }, "Account"),
      settingSelect("notify_gate.account", accountId, accountOptions(cfg), (v) => {
        const models = modelOptions(cfg, v);
        const current = s["notify_gate.model"];
        const body = { "notify_gate.account": v };
        if (!models.some((m) => m.id === current) && models[0]) body["notify_gate.model"] = models[0].id;
        saveSettings(body);
      }, "Gate account")),
    h("div", { class: "setting" }, h("div", { class: "label" }, "Model"),
      settingSelect("notify_gate.model", s["notify_gate.model"] || cfg.model, modelOptions(cfg, accountId),
        (v) => saveSetting("notify_gate.model", v), "Gate model")),
    h("div", { class: "setting" }, h("div", { class: "label" }, "Effort"),
      settingSelect("notify_gate.effort", s["notify_gate.effort"] || cfg.effort, EFFORT_OPTIONS,
        (v) => saveSetting("notify_gate.effort", v), "Gate effort")),
    recent.length ? h("details", {}, h("summary", { text: "Recent notification-gate decisions" }),
      // Headline first (the alert text, or the reason when nothing was sent), then one detail line.
      // Decision words come from the view when the server names them (decision_labels).
      h("ul", { class: "plain small gate-log" }, recent.slice(0, 15).map((r) => h("li", {},
        h("b", { text: r.title || r.reason || r.error || "No text" }),
        h("div", { class: "muted", text: [r.at ? clock(r.at) : null,
          cfg.decision_labels?.[r.decision] || r.decision || r.status, r.tier ? tierHead(r.tier) : null,
          r.title && r.reason ? r.reason : null].filter(Boolean).join(" · ") }),
        h("div", { class: "metric-meta",
          text: `${r.account || ""} ${r.model || ""} ${r.effort ? "· " + r.effort : ""}${r.error ? " · " + r.error : ""}` }))))) : h("p", { class: "muted small", text: "No gate decisions yet." }));
}


async function sendNtfyTest() {
  try {
    const data = await api(UI.api.notify_test, { method: "POST", body: {} });
    app.ntfyTest = data;
  } catch (err) {
    app.ntfyTest = { ok: false, detail: err.message || "could not send the test" };
  }
  render(true);
}

// The ntfy password is masked on every render; the real value only enters the
// DOM after "Show", and "Copy" copies it without revealing it.
const PASSWORD_MASK = "••••••••••";

function ntfyPasswordRow(password) {
  return h("div", { class: "secret-row muted small", "data-reading-key": "ntfy-password" }, "Password: ",
    h("span", { class: "secret", "data-secret": "ntfy-password", text: PASSWORD_MASK }),
    h("button", { class: "btn quiet", type: "button", "aria-pressed": "false", text: "Show", on: { click: (e) => {
      const button = e.currentTarget, value = button.parentElement.querySelector(".secret");
      const shown = button.getAttribute("aria-pressed") === "true";
      value.textContent = shown ? PASSWORD_MASK : password;
      button.setAttribute("aria-pressed", String(!shown)); button.textContent = shown ? "Show" : "Hide";
    } } }),
    h("button", { class: "btn quiet", type: "button", text: "Copy", on: { click: async (e) => {
      const status = e.currentTarget.parentElement.querySelector('[role="status"]');
      try { await navigator.clipboard.writeText(password); status.textContent = "Copied"; }
      catch (_) { status.textContent = "Could not copy"; }
      setTimeout(() => { status.textContent = ""; }, 2000);
    } } }), h("span", { class: "muted small", role: "status" }));
}

function ntfyCard(s, n) {
  if (!app.notifySetup && app.notifySetup !== false) {
    api(UI.api.notify_setup).then((data) => { app.notifySetup = data; render(true); })
      .catch((err) => { app.notifySetup = { configured: false, ready: false, detail: err.message }; render(true); });
    app.notifySetup = false;  // in flight
    return h("section", { class: "card" }, h("p", { class: "muted", text: "Loading notification setup…" }));
  }
  const d = app.notifySetup || {};
  const enabled = s["notify.enabled"] !== false;
  const status = d.ready ? "Ready to send" : (d.detail || n.detail || "Not set up yet");
  const rows = [
    h("div", { class: "setting" },
      h("div", { class: "label" }, "Send phone alerts via ntfy", h("small", { text: status })),
      toggle(!!enabled, "Send phone alerts via ntfy", (on) => { app.notifySetup = null; saveSetting("notify.enabled", on); }, "notify.enabled")),
  ];
  if (d.configured && d.deep_link) {
    rows.push(h("p", { class: "note", text: "On this phone, add the server in the ntfy app (login below), then tap Set up phone notifications." }));
    if (d.server) rows.push(h("p", { class: "muted small", text: "Server: " + d.server }));
    if (d.username) rows.push(h("p", { class: "muted small", text: "Username: " + d.username }));
    if (d.topic) rows.push(h("p", { class: "muted small", text: "Topic: " + d.topic }));
    if (d.password) rows.push(ntfyPasswordRow(d.password));
    rows.push(h("div", { class: "actions" },
      h("a", { class: "btn primary", href: d.deep_link, text: "Set up phone notifications" }),
      d.https_link ? h("a", { class: "btn", href: d.https_link, text: "Open the ntfy topic page" }) : null));
  } else if (d && d.configured === false) {
    rows.push(h("p", { class: "note", text: "ntfy is not configured on the laptop yet." }));
  }
  const result = app.ntfyTest;
  rows.push(h("div", { class: "actions" },
    h("button", { class: "btn", type: "button", on: { click: sendNtfyTest }, text: "Send test notification" }),
    result ? h("span", { class: result.ok ? "muted small" : "error", role: "status",
      text: result.ok ? "Server accepted the test notification." : ("Not sent: " + (result.detail || "failed")) }) : null));
  return h("section", { class: "card" }, rows);
}

// ---------------------------------------------------------------- phone alerts (v0.9.8.9)
// Alex 19 Nov: "make the notification to the phone easier to adapt and tune how i like".
// One place for what reaches the phone: on / always / paused, a snooze, each kind with its
// loudness and "only when I'm away", quiet hours, a rate limit with one digest, muted workspaces
// and a test. Everything comes from view.phone_prefs (notify_prefs.py); no secrets.
function phoneSegment(label, key, options, current, onPick) {
  return h("div", { class: "segmented", role: "group", "aria-label": label, "data-setting": key },
    options.map(([value, text]) => h("button", { type: "button", "aria-pressed": String(current === value),
      on: { click: () => { if (current !== value) onPick(value); } }, text })));
}

function phoneTypeRow(t, p, s) {
  const levels = p.levels || [];
  const level = s[`notify_tiers.${t.id}_level`] || t.level;
  const on = t.locked || t.enabled;
  const awayKey = `notify_tiers.${t.id}_away_only`;
  const away = s[awayKey] === undefined || s[awayKey] === null ? t.away_only : !!s[awayKey];
  const levelHint = (levels.find((l) => l.id === level) || {}).hint || "";
  return h("div", { class: `pa-type${on ? "" : " is-off"}`, "data-type": t.id, "data-setting": t.enabled_key },
    h("div", { class: "setting" },
      h("div", { class: "label" }, t.label, h("small", { text: t.hint })),
      t.locked ? lockedToggle(t.label) : toggle(on, t.label, (v) => saveSetting(t.enabled_key, v), t.enabled_key)),
    on ? h("div", { class: "pa-options" },
      phoneSegment(`${t.label}: loudness`, `notify_tiers.${t.id}_level`,
        levels.map((l) => [l.id, l.label]), level, (v) => saveSetting(`notify_tiers.${t.id}_level`, v)),
      h("small", { class: "muted pa-level-hint", text: levelHint }),
      t.locked ? null : h("label", { class: "pa-away" },
          toggle(away, `${t.label}: only when I'm away`, (v) => saveSetting(awayKey, v), awayKey),
          h("span", { text: "Only when I'm away from the laptop" }))) : null);
}

function phoneWorkspacesCard(s) {
  const list = (app.view.workspaces || []).filter((w) => w.watched !== false);
  if (!list.length) return null;
  const muted = new Set(s["watch.muted_workspaces"] || list.filter((w) => w.muted).map((w) => w.id));
  const save = (id, alerts) => {
    const next = new Set(muted);
    if (alerts) next.delete(id); else next.add(id);
    saveSetting("watch.muted_workspaces", [...next]);
  };
  return h("section", { class: "card pa-workspaces", "data-reading-key": "pa-workspaces" },
    h("h3", { class: "pa-title", text: "Workspaces" }),
    h("p", { class: "muted small", text: "Switch a workspace off to keep it on the dashboard without phone alerts." }),
    list.map((w) => h("div", { class: "setting", "data-setting": "watch.muted_workspaces", "data-workspace": w.id },
      h("div", { class: "label", text: w.label || w.id }),
      toggle(!muted.has(w.id), `${w.label || w.id}: phone alerts`, (v) => save(w.id, v)))));
}

function phoneAlertsSection(s, n) {
  const p = app.view.phone_prefs;
  if (!p) return null;
  const mode = s["presence.mode"] || p.mode || "auto";
  const snoozed = !!p.snooze_until;
  const q = p.quiet || {};
  const quietOn = s["notify_tiers.quiet_hours"] === undefined || s["notify_tiers.quiet_hours"] === null ? !!q.on : !!s["notify_tiers.quiet_hours"];
  const start = s["notify_tiers.quiet_start"] || q.start || "23:00";
  const end = s["notify_tiers.quiet_end"] || q.end || "08:00";
  const rate = p.rate || {};
  const count = s["notify.rate_count"] ?? rate.count ?? 3;
  const minutes = s["notify.rate_minutes"] ?? rate.minutes ?? 30;
  const result = app.ntfyTest;
  const types = (p.types || []).filter((t) => !t.advanced || t.enabled);
  return h("section", { id: "settings-phone_alerts", "data-reading-key": "settings-phone_alerts", class: "phone-alerts" },
    h("h2", { class: "section", text: "Phone alerts" }),
    h("section", { class: "card phone-status", "data-reading-key": "phone-status" },
      h("p", { role: "status", text: n.status_line || "Phone alert status unavailable" }),
      p.sent_24h !== null && p.sent_24h !== undefined ? h("p", { class: "muted small pa-count", text: `${plural(p.sent_24h, "alert")} sent in the last 24 hours.` }) : null,
      h("div", { class: "setting stack" }, h("div", { class: "label", text: "Phone" }),
        h("div", { class: "segmented", role: "group", "aria-label": "Phone", "data-setting": "presence.mode" },
          [["auto", "When I'm away"], ["always", "Always"], ["never", "Paused"]].map(([value, label]) =>
            h("button", { type: "button", "aria-pressed": String(mode === value), on: { click: () => saveSetting("presence.mode", value) }, text: label })))),
      h("p", { class: "muted small", text: mode === "always"
        ? "Always: every alert comes even while you use the laptop, so “Only when I'm away” below does nothing. Pick When I'm away to let calm ones wait."
        : mode === "never" ? "Paused: nothing is sent, not even questions. Alerts wait until you switch back."
          : "When I'm away: kinds marked “Only when I'm away” wait while you use the laptop. Questions and approvals always come." }),
      h("div", { class: "setting stack", "data-setting": "notify.snooze_until" },
        h("div", { class: "label" }, snoozed ? p.snooze_label : "Snooze",
          h("small", { text: "Questions and approvals still come through." })),
        h("div", { class: "pa-actions" }, snoozed
          ? h("button", { class: "btn", type: "button", text: "Resume now", on: { click: () => saveSetting("notify.snooze", "off") } })
          : [h("button", { class: "btn", type: "button", text: "1 hour", "aria-label": "Snooze for 1 hour", on: { click: () => saveSetting("notify.snooze", "1h") } }),
             h("button", { class: "btn", type: "button", text: `Until ${p.morning || "08:00"}`, "aria-label": "Snooze until morning", on: { click: () => saveSetting("notify.snooze", "morning") } })])),
      h("div", { class: "pa-actions" },
        h("button", { class: "btn", type: "button", on: { click: sendNtfyTest }, text: "Send test notification" }),
        result ? h("span", { class: result.ok ? "muted small" : "error", role: "status",
          text: result.ok ? "Sent. Check your phone." : ("Not sent: " + (result.detail || "failed")) }) : null)),
    h("section", { class: "card pa-types", "data-reading-key": "pa-types" },
      h("h3", { class: "pa-title", text: "What comes to your phone" }),
      h("p", { class: "muted small", text: "Quiet: no sound, just in the ntfy list. Normal: your usual sound. Urgent: sound and a pop-up." }),
      types.map((t) => phoneTypeRow(t, p, s))),
    h("section", { class: "card pa-when", "data-reading-key": "pa-when" },
      h("h3", { class: "pa-title", text: "When" }),
      h("div", { class: "setting" },
        h("div", { class: "label" }, `Quiet hours · ${start}–${end}`,
          h("small", { text: "Calm alerts wait until the morning. Questions and approvals still come." })),
        toggle(quietOn, "Quiet hours", (on) => saveSetting("notify_tiers.quiet_hours", on), "notify_tiers.quiet_hours")),
      h("div", { class: "tier-options" },
        h("label", {}, "From ", h("input", { type: "time", value: start, "aria-label": "Quiet hours start", "data-setting": "notify_tiers.quiet_start",
          on: { change: (e) => saveSetting("notify_tiers.quiet_start", e.target.value) } })),
        h("label", {}, "To ", h("input", { type: "time", value: end, "aria-label": "Quiet hours end", "data-setting": "notify_tiers.quiet_end",
          on: { change: (e) => saveSetting("notify_tiers.quiet_end", e.target.value) } }))),
      h("div", { class: "setting", "data-setting": "notify.rate_count" },
        h("div", { class: "label" }, "At most", h("small", { text: count ? "alerts, then the rest wait and come together as one summary" : "no limit" })),
        stepper(count, count === 1 ? "alert" : "alerts", 1, 0, 20, (v) => saveSetting("notify.rate_count", v), "At most")),
      h("div", { class: "setting", "data-setting": "notify.rate_minutes" },
        h("div", { class: "label" }, "Per", h("small", { text: "Questions and approvals do not count against it." })),
        stepper(minutes, "min", 5, 5, 240, (v) => saveSetting("notify.rate_minutes", v), "Per"))),
    phoneWorkspacesCard(s));
}

function renderSettings() {
  return h("div", { "data-reading-key": "settings" }, h("a", { class: "back", href: hashLink("home"), text: "Back" }),
    h("h1", { text: "Settings" }), h("p", { class: "muted small", text: "Changes save as you go." }), settingsBody());
}

const SETTINGS_SECTIONS = [["phone_alerts", "Phone alerts"], ["alerts", "Alert tiers"], ["workspaces", "Workspaces"], ["timing", "Timing"], ["ai", "AI use"],
  ["icons", UI.account_icons.labels.title], ["phone", "Phone setup"]];

function settingsBody() {
  const s = app.view.settings || {}, n = app.view.notify || {};
  const section = (id, ...children) => h("section", { id: `settings-${id}`, "data-reading-key": `settings-${id}` },
    h("h2", { class: "section", text: SETTINGS_SECTIONS.find(([key]) => key === id)[1] }), children);
  const gate = app.view.notify_tiers || {};
  const advanced = h("details", { class: "card ai-advanced", "data-reading-key": "ai-advanced" },
    h("summary", { text: "Advanced: AI check before sending" }),
    h("p", { class: "note", text: "When the AI check is on, each tier can be checked, reworded, or sent as is. When it is off, all alerts use their original wording." }),
    gate.estimate?.hint ? h("p", { class: "note", text: gate.estimate.hint }) : null,
    notifyGateCard(s),
    (gate.tiers || []).map(tier => h("div", { class: "setting" }, h("div", { class: "label", text: tier.label }),
      settingSelect(`notify_tiers.${tier.id}_gate`, s[`notify_tiers.${tier.id}_gate`] || tier.gate,
        (gate.gates || []).filter(g => !tier.lock_skip || g.id !== "check"),
        v => saveSetting(`notify_tiers.${tier.id}_gate`, v), `${tier.label}: AI check`))));
  return h("div", { "data-reading-key": "settings-body" },
    h("nav", { class: "chips settings-sections", "aria-label": "Settings sections" }, SETTINGS_SECTIONS.map(([id, label]) =>
      h("button", { class: "chip", type: "button", text: label, on: { click: () => document.getElementById(`settings-${id}`)?.scrollIntoView({ block: "start" }) } }))),
    phoneAlertsSection(s, n),
    section("alerts", notifyTiersCard(s)),
    h("section", { id: "settings-workspaces", "data-reading-key": "settings-workspaces" },
      h("div", { class: "section-head" }, h("h2", { class: "section", text: SETTINGS_SECTIONS.find(([key]) => key === "workspaces")[1] }), refreshWorkspacesButton()),
      chooserCard(), h("p", { class: "note", text: "New workspaces stay off until you switch them on." }), watchingCloselyCard()),
    section("timing", h("div", { class: "card" },
      h("div",{class:"setting","data-setting":"attention.coordinator_wait_minutes"},
        h("div",{class:"label"},"Escalate unanswered coordinator questions",h("small",{text:"0 min means off. Replies always go to the coordinator."})),
        stepper(s["attention.coordinator_wait_minutes"] || 0,"min",15,0,10080,v=>saveSetting("attention.coordinator_wait_minutes",v),"Coordinator question timeout")),
      h("div", { class: "setting", "data-setting": "stall.minutes" }, h("div", { class: "label" }, "Possible stall after", h("small", { text: "working, no new output" })),
        stepper(s["stall.minutes"], "min", 5, 5, 240, v => saveSetting("stall.minutes", v), "Possible stall after")),
      h("div", { class: "setting", "data-setting": "ui.parked_after_minutes" }, h("div", { class: "label" }, "Parked after", h("small", { text: "idle with nothing happening" })),
        stepper(s["ui.parked_after_minutes"], "min", 15, 15, 1440, v => saveSetting("ui.parked_after_minutes", v), "Parked after")),
      h("div", { class: "setting", "data-setting": "presence.phone_delay_minutes" }, h("div", { class: "label" }, "Phone alerts after", h("small", { text: "no laptop input for this long" })),
        stepper(s["presence.phone_delay_minutes"], "min", 1, 0, 120, v => saveSetting("presence.phone_delay_minutes", v), "Phone alerts after")))),
    section("ai", gateHealthLine(), h("div", { class: "card" },
      h("div", { class: "setting stack" }, h("div", { class: "label" }, "AI writing style",
        h("small", { text: "For summaries, AI-written notifications and Ask summary agent answers. Quotes and fixed names stay unchanged." })),
        settingSelect("writing.style", s["writing.style"] || app.view.writing?.style,
          app.view.writing?.options || [], v => saveSetting("writing.style", v), "AI writing style"))),
      summariesCard(s), advanced),
    section("icons", accountIconsCard("settings")),
    section("phone", ntfyCard(s, n)),
    // v0.9.7.4: only on the laptop itself (the desktop listener); the phone never gets this markup.
    app.view.features?.[UI.features.desktop_focus] ? tailnetAccessSection() : null,
    app.view.features?.[UI.features.desktop_focus] ? null : h("div", { class: "actions" }, h("button", { class: "btn quiet", type: "button", on: { click: logout }, text: "Log out" })));
}

// ---------------------------------------------------------------- tailnet allow list (v0.9.7.4)
// Which tailnet devices may reach the phone dashboard. Laptop only: GET/POST /api/tailnet-access
// answer 403 to the phone listener, and this section is built only on the desktop listener.
// Off (default) or an empty list = every tailnet device may open the login page (as before); off keeps
// the saved list for later. The login still applies.

function tailnetLoad(force = false) {
  const t = app.tailnet || (app.tailnet = { data: null, draft: null, manual: "", status: "", error: "" });
  if ((t.data && !force) || t.loading) return;
  t.loading = true;
  api(UI.api.tailnet_access).then((data) => {
    t.data = data;
    if (t.draftOn === undefined || t.draftOn === null) t.draftOn = !!data.enabled;
    if (t.draft === null) {
      // Pre-select: the saved list, else (first time) this laptop and every device that signed in recently.
      t.draft = data.entries.length ? [...data.entries]
        : data.peers.filter((p) => !p.self && p.signed_in_at).map((p) => p.name || p.ips[0]).filter(Boolean);
    }
  }).catch((err) => { t.error = `Could not read the tailnet devices: ${err.message}`; })
    .finally(() => { t.loading = false; render(true); });
}

function tailnetCovers(entries, peer) {
  const names = [peer.name, peer.dns, (peer.hostname || "").toLowerCase(), ...peer.ips];
  return entries.some((e) => names.includes(String(e).toLowerCase()));
}

function tailnetEdit(fn) {
  const t = app.tailnet;
  t.draft = fn([...(t.draft || [])]);
  t.status = "";
  render(true);
}

async function tailnetSave(entries, enabled) {
  const t = app.tailnet, data = t.data || { peers: [] };
  if (enabled && entries.length) {
    const left = data.peers.filter((p) => !p.self && p.signed_in_at && !tailnetCovers(entries, p));
    const warn = left.length
      ? `These devices signed in recently and would be locked out:\n\n${left.map((p) => `• ${p.name || p.hostname} (${p.os}, ${p.ips[0]})`).join("\n")}\n\nSave anyway?`
      : !data.peers.some((p) => !p.self && p.signed_in_at && tailnetCovers(entries, p))
        ? "No device on this list has signed in to the dashboard yet. Check that your phone is on it, or it will get 'forbidden'.\n\nSave anyway?"
        : null;
    if (warn && !window.confirm(warn)) return;
  }
  t.status = "Saving…";
  render(true);
  try {
    t.data = await api(UI.api.tailnet_access, { method: "POST", body: { entries, enabled } });
    t.draft = [...t.data.entries];
    t.draftOn = !!t.data.enabled;
    t.status = t.data.enforced ? "Saved. Only these devices can reach the dashboard over Tailscale."
      : t.data.enabled ? "Saved. Filtering is on but the list is empty, so every tailnet device can still open the login page."
      : "Saved. Filtering is off: every tailnet device can open the login page. The list is kept.";
  } catch (err) { t.status = `Could not save: ${err.message}`; }
  render(true);
}

function tailnetAccessSection() {
  tailnetLoad();
  const t = app.tailnet, data = t?.data;
  const head = h("h2", { class: "section", text: "Tailnet access" });
  if (!data) return h("section", { id: "settings-access", "data-reading-key": "settings-access" }, head,
    h("div", { class: "card" }, h("p", { class: "muted", text: t?.error || "Reading the tailnet devices…" })));
  const draft = t.draft || [], saved = data.entries, on = !!t.draftOn;
  const changed = JSON.stringify(draft) !== JSON.stringify(saved) || on !== !!data.enabled;
  const peerName = (p) => p.name || p.hostname || p.ips[0];
  const entryRow = (e) => {
    const resolved = data.resolved.find((r) => r.entry === e)?.ips;
    const peer = data.peers.find((p) => tailnetCovers([e], p));
    return h("li", { class: "access-entry", "data-reading-key": `access-entry:${e}` },
      h("span", { class: "access-name", text: e }),
      h("span", { class: "muted small", text: peer ? `${peer.os}${peer.ips[0] !== e ? ` · ${peer.ips[0]}` : ""}` : resolved ? "" : "not found on the tailnet" }),
      h("button", { class: "btn quiet", type: "button", text: "Remove", "aria-label": `Remove ${e}`,
        on: { click: () => tailnetEdit((list) => list.filter((x) => x !== e)) } }));
  };
  const peerRow = (p) => {
    const on = tailnetCovers(draft, p);
    return h("li", { class: `access-peer${p.online ? " online" : ""}`, "data-reading-key": `access-peer:${p.dns || p.ips[0]}` },
      h("i", { class: `dot ${p.online ? "s-done" : "s-parked"}`, "aria-hidden": "true" }),
      h("span", { class: "access-name", title: p.dns, text: peerName(p) }),
      h("span", { class: "muted small", text: [p.os, p.ips[0], p.online ? "online" : "offline",
        p.self ? "this laptop, always allowed" : null, p.signed_in_at ? `signed in ${ago(p.signed_in_at)} ago` : null].filter(Boolean).join(" · ") }),
      p.self ? null : h("button", { class: `btn${on ? " quiet" : ""}`, type: "button", text: on ? "Remove" : "Add", "aria-pressed": String(on),
        on: { click: () => tailnetEdit((list) => on ? list.filter((e) => !tailnetCovers([e], p)) : [...list, peerName(p)]) } }));
  };
  const manual = h("input", { type: "text", placeholder: "my-phone or 100.64.0.10", "aria-label": "Device name or tailnet IP",
    value: t.manual || "", on: { input: (e) => { t.manual = e.target.value; } } });
  const add = () => { const v = (t.manual || "").trim().toLowerCase(); if (!v) return; t.manual = "";
    tailnetEdit((list) => list.includes(v) ? list : [...list, v]); };
  return h("section", { id: "settings-access", "data-reading-key": "settings-access" }, head,
    h("div", { class: "card access-card" },
      h("p", { class: `access-state ${data.enforced ? "is-on" : "is-off"}`, role: "status", text: data.enforced
        ? `Filtering is ON: only the ${saved.length} listed device${saved.length === 1 ? "" : "s"} (and this laptop) can reach the dashboard over Tailscale. The login still applies.`
        : data.enabled ? "Filtering is on, but the list is empty: every tailnet device can open the login page."
        : `Filtering is OFF: every device on the tailnet can open the login page (with login).${saved.length ? ` The saved list (${saved.length}) is kept for when you turn it on.` : ""}` }),
      h("div", { class: "setting", "data-reading-key": "access-switch" },
        h("span", { class: "label" }, "Only allow the devices below", h("small", { text: "Off keeps the list but does not filter." })),
        toggle(on, "Only allow the listed tailnet devices", (v) => { t.draftOn = v; t.status = ""; render(true); })),
      h("p", { class: "muted small", text: "Shown and changed only on this laptop. Tailnet only, never Funnel." }),
      h("h3", { class: "sub", text: changed ? "Allowed devices (not saved yet)" : "Allowed devices" }),
      draft.length ? h("ul", { class: "access-list" }, draft.map(entryRow)) : h("p", { class: "muted small", text: "None: no limit." }),
      h("div", { class: "access-add" }, manual, h("button", { class: "btn", type: "button", text: "Add", on: { click: add } })),
      h("div", { class: "actions" },
        h("button", { class: "btn primary", type: "button", text: "Save", disabled: !changed || null,
          on: { click: () => tailnetSave(draft, on) } }),
        changed ? h("button", { class: "btn quiet", type: "button", text: "Undo changes",
          on: { click: () => { t.draftOn = !!data.enabled; tailnetEdit(() => [...saved]); } } }) : null,
        h("button", { class: "btn quiet", type: "button", text: "Refresh devices", on: { click: () => tailnetLoad(true) } })),
      t.status ? h("p", { class: "muted small", role: "status", text: t.status }) : null,
      h("details", { class: "access-peers", open: "" }, h("summary", { text: `Tailnet devices (${data.peers.length})` }),
        data.available ? h("ul", { class: "access-list" }, data.peers.map(peerRow)) : h("p", { class: "muted small", text: "tailscale status is not available." }))));
}

// ---------------------------------------------------------------- polling

// The dashboard owns its state colours (theme v0.8). Herdr's own status colours
// are only kept to tint the small dot on an agent's "Herdr status" line.
function applyStatusColors(view) {
  if (view.state_definitions) { STATES=view.state_definitions.map(s=>s.id);for(const state of view.state_definitions) {STATE_LABEL[state.id]=state.label;STATE_SHORT[state.id]=state.short;} }
  const c = (view.status_colors && view.status_colors.colors) || {};
  app.herdrColors = {};
  for (const [name, value] of Object.entries(c)) if (value && /^#[0-9a-f]{6}$/i.test(value)) app.herdrColors[name] = value;
}

// v0.9.4: the colours follow the Omarchy theme live (herdr_theme.py). The page loads theme.css once;
// when the server reports a new theme key, the new sheet is loaded next to the old one, which is
// removed only when the new one is in (no flash of the fallback colours).
function applyTheme(view) {
  const theme = view.theme, link = $("theme-css");
  if (!theme || !theme.key || !link) return;
  if (!link.dataset.key) link.dataset.key = theme.key;
  else if (link.dataset.key !== theme.key) {
    const next = document.createElement("link");
    next.rel = "stylesheet";
    next.href = `theme.css?k=${encodeURIComponent(theme.key)}`;
    next.addEventListener("load", () => {
      for (const old of document.querySelectorAll('link[href^="theme.css"]')) if (old !== next) old.remove();
      next.id = "theme-css";
    }, { once: true });
    next.dataset.key = link.dataset.key = theme.key;
    link.after(next);
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta && theme.tokens && /^#[0-9a-f]{6}$/i.test(theme.tokens.bg || "")) meta.content = theme.tokens.bg;
}

function herdrStatusColor(status) {
  return (app.herdrColors || {})[status === "needs_you" ? "blocked" : status] || null;
}

// One evidence line; the "Herdr status" line carries a dot in Herdr's own colour.
function evidenceItem(line, agent) {
  const status = agent && agent.evidence && agent.evidence.herdr_status;
  const color = status && line.startsWith("Herdr status:") ? herdrStatusColor(status) : null;
  if (!color) return h("li", { text: line });
  return h("li", {}, h("i", { class: "herdr-dot", "aria-hidden": "true", style: { background: color } }), line);
}

// v0.9.7.5: an open page (the desktop window, a phone tab) kept running the code it was loaded with
// after an upgrade: Alex's laptop window still drew the pre-0.9.7 CC / CX pills instead of account
// icons. The page now notes the collector version it started with and reloads once when the server
// reports another one. Not while Alex is typing or a reply sheet is open: then on the next refresh.
// sessionStorage stops a reload loop if the new page somehow reports the old version again.
function reloadIfUpgraded(view, { reload = () => location.reload(), storage = window.sessionStorage } = {}) {
  const version = view?.collector?.version;
  if (!version) return false;
  if (!app.loadedVersion) { app.loadedVersion = version; return false; }
  if (version === app.loadedVersion) return false;
  const active = document.activeElement;
  if (app.composer || (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName))) return false;
  try { if (storage.getItem("hat-reloaded-for") === version) return false; storage.setItem("hat-reloaded-for", version); } catch (_) {}
  reload();
  return true;
}

async function refresh(force = false) {
  const seq = (app.refreshSeq = (app.refreshSeq || 0) + 1);
  try {
    const view = await api(UI.api.view);
    if (seq !== app.refreshSeq) return;          // a newer refresh already answered
    if (reloadIfUpgraded(view)) return;
    // v0.9.8.3: a workspace was added, closed or renamed: the Settings list reloads.
    if (app.view && view.chooser_rev && app.view.chooser_rev && view.chooser_rev !== app.view.chooser_rev) app.chooser = null;
    app.view = view;
    if (view.features?.[UI.features.desktop_focus]) {
      document.title = UI.desktop.title;
      if (!app.desktopNavigationTimer) {
        desktopNavigation();
        app.desktopNavigationTimer = setInterval(desktopNavigation, 1000);
      }
    }
    applyStatusColors(view);
    applyTheme(view);
    app.lastOk = Date.now();
    app.error = null;
    updateNotificationBadge($("notif-badge"));
    render(force);
    const nr = route();
    if (nr.name === "notifications" && (now() - (app.notifications?.at || 0)) * 1000 > POLL_MS / 2) loadNotifications(nr.id);
  } catch (err) {
    if (err.message === "login required") return;
    app.error = err.message;
    renderFresh();
  }
  const r = route();
  if (r.name === "agent" && app.details[r.id] && (now() - app.details[r.id].generated_at) * 1000 > DETAIL_MS) loadDetail(r.id);
}

function startPolling() {
  stopPolling();
  app.timer = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, POLL_MS);
  app.ticker = setInterval(renderFresh, 5000);
}

function stopPolling() {
  clearInterval(app.timer);
  clearInterval(app.ticker);
}


// ---------------------------------------------------------------- reply / ask (never automatic)

function agentById(identity) {
  return (app.view && app.view.agents || []).find((a) => a.identity === identity) || null;
}

// At an approval or question screen Herdr only takes keys typed in the
// terminal; a reply from here waits until the agent leaves that screen.
const TERMINAL_HINT = "Answer this in the terminal. A reply from here is only delivered after the agent leaves that screen.";

function atQuestionScreen(a) {
  return !!a && a.present !== false && (a.status === "blocked" || a.kind === "approval" || a.kind === "question");
}

function replyButton(a, primary = false) {
  return easyReplyButton(a,{primary});
}

function replyRecipient(a) {
  if (a?.reply_target) return a.reply_target;
  const seen=new Set();let target=a;
  while(target && !seen.has(target.identity)) {
    seen.add(target.identity);
    const parent=target.reports_to?.identity || app.view.links?.find(l=>l.kind==='reports_to' && l.from===target.identity)?.to || target.in_process_parent;
    if(!parent) return {identity:target.identity,name:displayName(target),workspace_label:target.workspace_label,tab_label:target.tab_label,status:target.status};
    target=agentById(parent);
  }
  return {error:'Coordinator unavailable'};
}

function easyReplyButton(a,{question=null,anchor=null,eventId=null,primary=true}={}) {
  if(!a || !app.view.features?.reply) return null;
  return h('button',{class:`btn reply-open${primary?' primary':''}`,type:'button',text:UI.easy_reply.labels.reply,
    disabled:!!replyRecipient(a).error,'aria-label':`Reply to ${replyRecipient(a).name || displayName(a)}`,
    on:{click:event=>{event.preventDefault();event.stopPropagation();
      const key=anchor || event.currentTarget.closest('[data-reply-anchor]')?.dataset.replyAnchor || `agent:${a.identity}`;
      openReply(a.identity,question?.text,eventId,question?.id,{anchor:key});}}});
}

function readDraft(key) {
  try{return JSON.parse(localStorage.getItem(UI.easy_reply.draft_storage_key) || '{}')[key] || '';}catch(_){return '';}
}

function saveDraft(key,text) {
  try{const drafts=JSON.parse(localStorage.getItem(UI.easy_reply.draft_storage_key) || '{}');
    if(text) drafts[key]=text;else delete drafts[key];
    localStorage.setItem(UI.easy_reply.draft_storage_key,JSON.stringify(drafts));}catch(_){}
}

function replyDraftKey(c) {return c.questionId?`question:${c.questionId}`:`${c.steer?'steer':'reply'}:${c.identity}`;}
// "To: [icon] web-lead (Website / Lead)": recipientLabel with the recipient's account icon (v0.9.7).
function recipientChip(target) {
  const to = UI.easy_reply.labels.to, text = recipientLabel(target);
  const icon = target?.identity ? app.view?.agents?.find((x) => x.identity === target.identity)?.icon : null;
  const mark = accountIcon(icon, { size: 14, text: false });
  return h("p", { class: "recipient-chip muted small" }, ...(mark && text.startsWith(`${to} `)
    ? [`${to} `, h("span", { class: "route-who" }, mark, text.slice(to.length + 1))] : [text]));
}

function recipientLabel(target) {
  return `${UI.easy_reply.labels.to} ${target.name || 'Coordinator unavailable'} (${[target.workspace_label,target.tab_label].filter(Boolean).join(' / ') || 'workspace unavailable'})`;
}

// v0.9.6: Alex's own last reply from the dashboard, only while it is newer than
// the agent's current message: agent.last_reply {text, at, to}. Otherwise nothing.
function replyReceipt(a) {
  const reply=a?.last_reply;
  if(!reply?.text || !reply?.at) return null;
  return h('p',{class:'reply-delivery muted small',role:'status','aria-live':'polite',text:repliedText(reply,a)});
}

function replySlot(key,a) {
  const c=app.composer;
  return h('div',{class:'reply-slot','data-reply-slot':key,'data-reading-key':`reply-slot:${key}`},
    c?.kind==='reply' && !c.steer && c.anchor===key?easyReplyPanel(c):replyReceipt(a));
}

function easyReplyPanel(c) {
  const target=c.target || replyRecipient(agentById(c.identity));
  return h('form',{class:'easy-composer','data-reading-key':`easy:${c.identity}:${c.questionId || ''}`,
    'aria-label':`Reply to ${target.name}`,on:{submit:e=>{e.preventDefault();confirmReply();}}},
    h('div',{class:'reply-row-head'},h('strong',{class:'recipient-chip',text:recipientLabel(target)}),
      h('button',{class:'btn quiet',type:'button',text:'Close',on:{click:closeComposer}})),
    c.quote?h('p',{class:'reply-context muted small',text:c.quote}):null,
    c.terminalText ? h('p',{class:'muted small',text:'Send answers the current question.'}) : target.status==='working' || atQuestionScreen(agentById(target.identity))?h('p',{class:'muted small',text:'Queues until the agent can receive it; does not interrupt.'}):null,
    h('label',{},'Your reply',h('textarea',{rows:'4','aria-label':'Your reply',enterkeyhint:'send',
      on:{input:e=>{c.text=e.target.value;saveDraft(replyDraftKey(c),c.text);updateReplyPreview();},
        keydown:e=>{if(e.key==='Enter' && !e.shiftKey && !e.isComposing){e.preventDefault();confirmReply();}}}},c.text || '')),
    c.error?h('p',{class:'error',role:'alert',text:c.error}):null,
    h('div',{class:'actions reply-send-bar'},h('span',{class:'reply-count muted small'}),
      h('button',{class:'btn primary reply-confirm',type:'submit',disabled:!!c.busy || !c.text?.trim(),text:c.busy?'Sending…':UI.easy_reply.labels.send})));
}

function positionReplyComposer() {
  const v=window.visualViewport;
  document.documentElement.style.setProperty('--reply-keyboard',`${v?Math.max(0,innerHeight-v.height-v.offsetTop):0}px`);
  document.documentElement.style.setProperty('--reply-viewport',`${v?.height || innerHeight}px`);
}
window.addEventListener('pagehide',()=>{if(app.composer?.kind==='reply') saveDraft(replyDraftKey(app.composer),app.composer.text || '');});
window.visualViewport?.addEventListener('resize',positionReplyComposer);
window.visualViewport?.addEventListener('scroll',positionReplyComposer);

async function sendQuestionReply(q,text) {
  if(!text?.trim() || app.replySending?.[q.id]) return;
  const a=agentById(q.identity),target=replyRecipient(a);
  app.replySending ||= {};app.replySending[q.id]=true;
  let option=q.options?.find(o=>o===text);
  if(q.terminal && !option && /^(yes|no)$/i.test(text)) {
    const matches=(q.options || []).filter(o=>new RegExp(`^\\s*\\d+[.)]\\s*${text}\\b`,'i').test(o));
    if(matches.length===1) option=matches[0];
  }
  try {
    if(q.terminal && q.options?.length && target.identity===q.identity && /^(yes|no)$/i.test(text) && !option) throw new Error('Choose the exact dialog option; Yes/No is ambiguous here.');
    const result=await api(UI.api.replies,{method:'POST',body:{identity:q.identity,recipient_identity:target.identity,text,
      quote:q.text,question_id:q.id,confirm:true,when:'auto',interrupt:false,
      option:q.terminal && target.identity===q.identity?option:undefined,
      terminal_text:q.terminal && !q.options?.length && target.identity===q.identity}});
    if(result.status==='failed') throw new Error(result.error || 'Reply failed');
    recordReplyResult(result);saveDraft(`question:${q.id}`,'');delete app.questionDrafts?.[q.id];
    if(document.activeElement?.closest('.question-card')?.getAttribute('data-reading-key')===`question:${q.id}`) document.activeElement.blur();
    await refresh(true);
  } catch(err){flash(`Not sent: ${err.message}`);}
  finally{delete app.replySending[q.id];render(true);}
}

function recordReplyResult(result) {
  app.replyReceipts ||= {};app.replyReceipts[result.identity]=result;
  app.lastReplyIdentity=result.identity;app.dismissedReply=null;
  const target=result.recipient || {name:result.agent_name};
  flash(result.status==='queued'?`${UI.easy_reply.labels.queued} · To: ${target.name}`:`${UI.easy_reply.labels.sent} ${target.name}`);
}

function renderReplyDelivery() {
  const root=$('reply-deliveries');if(!root || !app.view) return;
  // The newest last reply among the recipient and the agents whose replies go to it.
  const a=app.view.agents.filter(a=>(a.reply_target?.identity===app.lastReplyIdentity || a.identity===app.lastReplyIdentity) && a.last_reply?.text && a.last_reply.at)
    .sort((x,y)=>y.last_reply.at-x.last_reply.at)[0];
  const id=a ? `${a.identity}:${a.last_reply.at}` : null;
  if(!id || app.dismissedReply===id || app.composer) {root.hidden=true;return;}
  root.hidden=false;
  updateReadingView(root,h('div',{class:'reply-row-head'},replyReceipt(a),
    h('button',{class:'btn quiet',type:'button',text:'Dismiss','aria-label':'Dismiss delivery status',on:{click:()=>{app.dismissedReply=id;root.hidden=true;}}})));
}

function watchCloselyList() {
  const cfg = (app.view && app.view.notify_tiers) || {};
  return Array.isArray(cfg.watch_closely) ? cfg.watch_closely.slice() : [];
}

function watchBadge(a) {
  return watchCloselyList().includes(a.identity) ? h("span", { class: "watch-badge", title: "Watching closely", "aria-label": "Watching closely" }, icon("eye")) : null;
}

function watchingCloselyCard() {
  const pins = watchCloselyList();
  const gone = pins.filter(id => !agentById(id)?.present);
  return h("section", { class: "card watching-closely", "data-reading-key": "watching-closely" },
    h("h3", { class: "sub", text: `Watching closely · ${pins.length}` }),
    h("p", { class: "muted small", text: "Every alert tier for these agents, until you stop. Quiet hours still allow their alerts; Paused holds delivery." }),
    pins.length ? h("ul", { class: "plain" }, pins.map(id => {
      const agent = agentById(id), present = !!agent?.present;
      return h("li", { class: "watching-row", "data-reading-key": `pin:${id}` },
        h("div", {}, present ? h("a", { href: hashLink("agent", id), text: displayName(agent) })
          : h("span", { text: agent ? displayName(agent) : "Agent no longer running" }),
          h("div", { class: "muted small", text: present ? "Every alert, until you stop" : "Gone" })),
        h("button", { class: "btn quiet", type: "button", "aria-label": `Stop watching ${agent ? displayName(agent) : "gone agent"}`,
          on: { click: () => toggleWatchClosely(id) }, text: "Stop" }));
    })) : h("p", { class: "muted small", text: "No agents watched closely." }),
    gone.length ? h("button", { class: "btn", type: "button", text: `Clear gone agents (${gone.length})`,
      on: { click: () => saveSetting("notify_tiers.watch_closely", pins.filter(id => !gone.includes(id))) } }) : null);
}

function watchCloselyButton(a) {
  if (!a || !a.identity) return null;
  const pinned = watchCloselyList().includes(a.identity);
  return h("button", { class: "btn watch-toggle", type: "button", "aria-pressed": String(pinned),
    title: UI.watch_closely.labels.hint,
    on: { click: (event) => { event.preventDefault(); event.stopPropagation(); toggleWatchClosely(a.identity); } },
    text: pinned ? "Watching closely" : "Watch closely" });
}

async function toggleWatchClosely(identity) {
  const next = watchCloselyList();
  const i = next.indexOf(identity);
  if (i >= 0) next.splice(i, 1); else next.push(identity);
  await saveSetting("notify_tiers.watch_closely", next);
}

function openAsk(identity, selection) {
  const question = selection ? "What does this mean, and what should I do about it?" : "";
  app.composer = { kind: "ask", identity, quote: selection || "", question, job: null, error: null };
  renderComposer();
}

function localReplyPreview(agent) {
  const status = (agent && agent.status) || "unknown";
  const present = !!(agent && agent.present);
  const pane = agent && agent.pane_id;
  let warning = null, default_when = "now", can_send_now = true, now_reason = null;
  if (!present || !pane) {
    warning = "This agent is no longer running, so a reply cannot be sent.";
    now_reason = "the agent is no longer running";
    can_send_now = false; default_when = "when_waiting";
  } else if (atQuestionScreen(agent)) {
    warning = TERMINAL_HINT;
    now_reason = "the agent is at an approval or question screen";
    default_when = "when_waiting"; can_send_now = false;
  } else if (status === "working") {
    warning = "This agent is working. Sending now can interrupt the current turn. The default is to send when it is next waiting (idle or finished).";
    default_when = "when_waiting";
  }
  return { name: agent && (agent.name || agent.label), pane_id: pane, status, present, warning, default_when, can_send_now, now_reason };
}

function openReply(identity, selection, eventId = null, questionId = null, options={}) {
  if(app.composer?.kind==='reply') saveDraft(replyDraftKey(app.composer),app.composer.text || '');
  const source = agentById(identity) || {};
  const target = replyRecipient(source);
  const agent = agentById(target.identity) || source;
  const preview = localReplyPreview(agent);
  app.composer = { kind: "reply", identity, target, steer:!!options.steer, anchor:options.anchor || `agent:${identity}`, eventId, questionId, quote: selection || "", includeQuote: true, text: "", preview, server: null,
    terminalText:!!questionId && !!app.view.questions?.find(q=>q.id===questionId && q.terminal && !q.options?.length) && target.identity===identity,
    when: preview.default_when, interrupt: false, error: null, busy: false };
  app.composer.text=readDraft(replyDraftKey(app.composer));
  if(!options.steer) render(true);
  renderComposer();
  const box=document.querySelector('.easy-composer textarea') || $('composer')?.querySelector('textarea');
  box?.focus({preventScroll:true});
  if(!options.steer) box?.closest('.easy-composer')?.scrollIntoView({block:'nearest'});
}

function replyQuote(c) {
  return c.includeQuote && c.quote ? c.quote : "";
}

// The server builds the payload (reply.payload), so what is shown is what is sent.
function scheduleReplyPreview() {
  clearTimeout(scheduleReplyPreview.timer);
  scheduleReplyPreview.timer = setTimeout(fetchReplyPreview, 200);
}

async function fetchReplyPreview() {
  const c = app.composer;
  if (!c || c.kind !== "reply" || !c.steer) return;
  const text = (c.text || "").trim();
  if (!text) { c.server = null; c.previewError = null; updateReplyPreview(); return; }
  const asked = `${text}\u0000${replyQuote(c)}`;
  c.previewFor = asked;
  try {
    const result = await api(UI.api.replies, { method: "POST", body: { identity: c.identity, text, quote: replyQuote(c) } });
    if (app.composer !== c || c.previewFor !== asked) return;     // typed on meanwhile
    c.server = result; c.previewError = null;
  } catch (err) {
    if (app.composer !== c || c.previewFor !== asked) return;
    c.server = null; c.previewError = err.message;
  }
  updateReplyPreview();
}

function replyMax(c) {
  return (c.server && c.server.max_text) || (app.view && app.view.features && app.view.features.reply_max_text) || null;
}

function replyReady(c) {
  if(!c.steer) return !!(c.text?.trim() && c.text.trim().length<=replyMax(c) && !c.busy && !c.target?.error);
  const fresh = c.server && c.previewFor === `${(c.text || "").trim()}\u0000${replyQuote(c)}`;
  return !!(fresh && !c.server.too_long && !c.busy);
}

// Patch only the preview, count and Confirm button so the textarea keeps focus.
function updateReplyPreview() {
  const c = app.composer;
  const root = c?.steer ? $('composer') : document.querySelector('.easy-composer');
  if (!c || c.kind !== "reply" || !root) return;
  if(!c.steer) {
    const send=root.querySelector('.reply-confirm'),count=root.querySelector('.reply-count');
    if(send) send.disabled=!replyReady(c);
    if(count) count.textContent=`${(c.text || '').length} / ${replyMax(c) || 8000}`;
    return;
  }
  const box = root.querySelector(".reply-payload");
  const count = root.querySelector(".reply-count");
  const confirm = root.querySelector(".reply-confirm");
  const max = replyMax(c);
  if (box) {
    box.classList.toggle("empty", !c.server);
    box.textContent = c.server ? c.server.payload
      : c.previewError ? `No preview: ${c.previewError}` : "Write a reply to see exactly what will be sent.";
  }
  if (count) {
    const over = !!(c.server && c.server.too_long);
    count.classList.toggle("over", over);
    count.textContent = c.server ? `${c.server.length} / ${max ?? "?"} characters${over ? " · too long, shorten it to send" : ""}`
      : max ? `limit ${max} characters` : "";
  }
  if (confirm) confirm.disabled = !replyReady(c);
}

async function confirmReply() {
  const c = app.composer;
  if (!c || c.kind !== "reply" || c.busy) return;
  const text = (c.text || "").trim();
  if (!text) { c.error = "Write a reply first."; renderComposer(); return; }
  if (!replyReady(c)) { c.error = c.steer?"Wait for the preview of what will be sent.":"Shorten the reply before sending."; renderComposer(); return; }
  c.busy = true; c.error = null; renderComposer();
  try {
    const result = await api(UI.api.replies, { method: "POST", body: {
      identity: c.identity, text, quote: replyQuote(c), confirm: true,
      when: c.steer ? c.when || "when_waiting" : "auto",
      recipient_identity:c.target?.identity,
      terminal_text:!c.steer && c.terminalText,
      interrupt: c.steer && !!c.interrupt,
      question_id: c.questionId ?? undefined,
      event_id: c.eventId ?? undefined,       // the server marks the answered notification read
    } });
    if(result.status==='failed') throw new Error(result.error || 'Reply failed');
    recordReplyResult(result);
    saveDraft(replyDraftKey(c),'');
    if(c.questionId) delete app.questionDrafts?.[c.questionId];
    document.querySelector('.easy-composer textarea')?.blur();
    app.composer = null;
    delete app.details[c.identity];
    renderComposer();
    if (route().name === "notifications") loadNotifications(route().id);
    await refresh(true);
  } catch (err) {
    c.busy = false;
    c.error = err.message;
    renderComposer();
  }
}

async function submitAsk() {
  const c = app.composer;
  if (!c || c.kind !== "ask" || c.busy) return;
  const question = (c.question || "").trim() || "What does this mean, and what should I do about it?";
  c.busy = true; c.error = null; renderComposer();
  try {
    const result = await api(UI.api.summaries, { method: "POST", body: {
      identity: c.identity, mode: "ask", question, context: c.quote || "",
    } });
    if (result.status === "skipped") throw new Error(result.reason || "not queued");
    c.job = result;
    pollAsk(result.id);
  } catch (err) {
    c.busy = false;
    c.error = err.message;
    renderComposer();
  }
}

async function pollAsk(id) {
  const started = Date.now();
  while (app.composer && app.composer.kind === "ask" && app.composer.job && app.composer.job.id === id) {
    try {
      const job = await api(`${UI.api.summaries}?id=${id}`);
      app.composer.job = job;
      app.composer.busy = job.status === "queued" || job.status === "running";
      renderComposer();
      if (job.status === "complete" || job.status === "failed" || job.status === "skipped") return;
    } catch (err) {
      app.composer.error = err.message;
      app.composer.busy = false;
      renderComposer();
      return;
    }
    if (Date.now() - started > 180000) {
      app.composer.error = "The ask is taking too long. Check back in the summary audit.";
      app.composer.busy = false;
      renderComposer();
      return;
    }
    await new Promise((ok) => setTimeout(ok, 1000));
  }
}

function closeComposer() {
  if(app.composer?.kind==='reply') saveDraft(replyDraftKey(app.composer),app.composer.text || '');
  app.composer = null;
  renderComposer();
  render(true);
}

// A tap beside the sheet (easy with one hand) must not throw away typed text;
// Cancel or Close still closes it.
function composerHasDraft() {
  const c = app.composer;
  return !!c && !!String((c.kind === "ask" ? c.question : c.text) || "").trim();
}

// Patch an open composer in place, including while an ask job completes.
function renderComposer() {
  buildComposer();
}

function buildComposer() {
  let root = $("composer");
  if (!root) {
    root = h("div", { id: "composer" });
    document.body.appendChild(root);
  }
  const c = app.composer;
  if (!c) { root.replaceChildren(); root.hidden = true; return; }
  if(c.kind==='reply' && !c.steer) {
    renderReplyDelivery();
    positionReplyComposer();
    const slot=[...document.querySelectorAll('[data-reply-slot]')].find(el=>el.dataset.replySlot===c.anchor);
    root.hidden=!!slot;
    const host=slot || root;
    if(!slot) root.className='easy-reply-fallback';
    updateReadingView(host,easyReplyPanel(c));
    const box=host.querySelector('textarea');
    if(box && box.value!==(c.text || '')) box.value=c.text || '';
    updateReplyPreview();
    return;
  }
  root.className='';
  const agent = agentById(c.target?.identity || c.identity) || { identity: c.identity, name: c.identity, pane_id: "?", status: "unknown" };
  root.hidden = false;
  if (c.kind === "ask") {
    const job = c.job;
    const answer = job && job.status === "complete" && job.summary ? (job.summary.answer || "") : "";
    updateReadingView(root, h("div", { class: "modal-backdrop", "data-reading-key": `composer:${c.kind}:${c.identity}`, on: { click: (e) => { if (e.target === e.currentTarget && !composerHasDraft()) closeComposer(); } } },
      h("div", { class: "modal", role: "dialog", "aria-label": "Ask the summary agent" },
        h("h2", { text: "Ask the summary agent" }),
        h("p", { class: "muted small", text: `About ${displayName(agent)}. Uses your current summary account, model and effort. Nothing is sent to the coding agent.` }),
        c.quote ? h("blockquote", { class: "quote", text: c.quote }) : null,
        h("label", {}, "Your question",
          h("textarea", { rows: "4", "aria-label": "Question", on: { input: (e) => { c.question = e.target.value; } } })),
        answer ? h("div", { class: "ask-answer" }, h("h3", { class: "sub", text: "Answer" }), h("p", { text: answer })) : null,
        job && (job.status === "queued" || job.status === "running") ? h("p", { class: "muted", text: "Asking…" }) : null,
        job && job.status === "failed" ? h("p", { class: "error", text: job.error || "The ask failed." }) : null,
        c.error ? h("p", { class: "error", role: "alert", text: c.error }) : null,
        h("div", { class: "actions" },
          h("button", { class: "btn primary", type: "button", disabled: !!c.busy, on: { click: submitAsk }, text: c.busy ? "Asking…" : (answer ? "Ask again" : "Ask") }),
          h("button", { class: "btn quiet", type: "button", on: { click: closeComposer }, text: "Close" })))));
    const box = root.querySelector("textarea");
    if (box && box.value !== (c.question || "")) box.value = c.question || "";
    return;
  }
  const prev = c.preview || {};
  const working = (prev.status || agent.status) === "working";
  const canNow = prev.can_send_now !== false;
  const when = canNow ? (c.when || prev.default_when || "when_waiting") : "when_waiting";
  c.when = when;
  updateReadingView(root, h("div", { class: "modal-backdrop", "data-reading-key": `composer:${c.kind}:${c.identity}`, on: { click: (e) => { if (e.target === e.currentTarget && !composerHasDraft()) closeComposer(); } } },
    h("div", { class: "modal", role: "dialog", "aria-label": "Confirm reply" },
      h("h2", { text: UI.easy_reply.labels.steer }),
      h("p",{class:"recipient-chip",text:recipientLabel(c.target || replyRecipient(agent))}),
      h("p", { class: "confirm-target" },
        "This will send to ", h("b", { text: displayName(agent) }),
        prev.pane_id || agent.pane_id ? ` (pane ${prev.pane_id || agent.pane_id})` : "",
        ` · status: ${prev.status || agent.status}.`),
      prev.warning ? h("p", { class: "note warn-send", text: prev.warning }) : null,
      c.quote ? h("label", { class: "switch-inline reply-include" },
        h("input", { type: "checkbox", class: "reply-include-box", checked: c.includeQuote }), " Include the quoted text") : null,
      c.quote ? h("blockquote", { class: `quote${c.includeQuote ? "" : " excluded"}`, text: c.quote }) : null,
      h("label", {}, "Your reply",
        h("textarea", { rows: "5", "aria-label": "Your reply", on: { input: (e) => { c.text = e.target.value; saveDraft(replyDraftKey(c),c.text); updateReplyPreview(); scheduleReplyPreview(); } } })),
      h("div", { class: "reply-preview" },
        h("div", { class: "reply-preview-head" },
          h("span", { text: "This is exactly what will be sent" }),
          h("span", { class: "reply-count", "aria-live": "polite" })),
        h("pre", { class: "reply-payload", tabindex: "0" })),
      h("fieldset", { class: "setting stack reply-when" },
        h("legend", { class: "sr-only", text: "When to send" }),
        h("label", {}, h("input", { type: "radio", name: "when", value: "when_waiting", checked: when !== "now" }),
          " Send when it is waiting (idle or finished)"),
        h("label", { class: canNow ? null : "disabled" },
          h("input", { type: "radio", name: "when", value: "now", checked: when === "now", disabled: !canNow }),
          " Send now", canNow ? (working ? " (this can interrupt the current turn)" : "")
            : h("span", { class: "muted small", text: ` — not possible: ${prev.now_reason || "the agent cannot take it now"}` }))),
      working && when === "now"
        ? h("label", { class: "switch-inline" }, h("input", { type: "checkbox", class: "reply-interrupt", checked: !!c.interrupt }),
            " I understand this may interrupt the agent")
        : null,
      c.error ? h("p", { class: "error", role: "alert", text: c.error }) : null,
      h("p", { class: "muted small", text: "Nothing is sent until you confirm. A record is added to the agent timeline." }),
      h("div", { class: "actions" },
        h("button", { class: "btn primary reply-confirm", type: "button", on: { click: confirmReply }, text: c.busy ? "Sending…" : "Confirm and send" }),
        h("button", { class: "btn quiet", type: "button", on: { click: closeComposer }, text: "Cancel" })))));
  const box = root.querySelector("textarea");
  if (box && box.value !== (c.text || "")) box.value = c.text || "";
  updateReplyPreview();
  for (const radio of root.querySelectorAll('input[name="when"]')) {
    radio.onchange = () => {
      if (!radio.checked) return;
      c.when = radio.value;
      if (c.when !== "now") c.interrupt = false;
      renderComposer();
    };
  }
  const include = root.querySelector(".reply-include-box");
  if (include) include.onchange = () => { c.includeQuote = include.checked; c.server = null; renderComposer(); scheduleReplyPreview(); };
  const chk = root.querySelector(".reply-interrupt");
  if (chk) chk.onchange = () => { c.interrupt = chk.checked; };
}

function selectedText() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) return "";
  return String(sel).trim();
}

function selectionSource() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  let node = sel.getRangeAt(0).commonAncestorContainer;
  if (node.nodeType === 3) node = node.parentElement;
  const host = node && node.closest ? node.closest("[data-selectable]") : null;
  if (!host) return null;
  const text = selectedText();
  if (!text) return null;
  return { identity: host.getAttribute("data-agent-identity"), source: host.getAttribute("data-selectable"), text, rect: sel.getRangeAt(0).getBoundingClientRect() };
}

function hideSelectBar() {
  const bar = $("select-bar");
  if (bar) bar.hidden = true;
}

function showSelectBar() {
  const found = selectionSource();
  let bar = $("select-bar");
  if (!found || !found.identity) { hideSelectBar(); return; }
  if (!bar) {
    bar = h("div", { id: "select-bar", class: "select-bar", hidden: true });
    document.body.appendChild(bar);
  }
  bar.hidden = false;
  bar.replaceChildren(
    h("button", { class: "btn", type: "button", on: { mousedown: (e) => e.preventDefault(), click: () => { openAsk(found.identity, found.text); window.getSelection()?.removeAllRanges(); hideSelectBar(); } }, text: "Ask summary agent" }),
    atQuestionScreen(agentById(found.identity)) ? null : h("button", { class: "btn", type: "button", on: { mousedown: (e) => e.preventDefault(), click: () => { openReply(found.identity, found.text); window.getSelection()?.removeAllRanges(); hideSelectBar(); } }, text: "Reply to agent…" }),
  );
  const top = Math.max(8, window.scrollY + found.rect.bottom + 8);
  const left = Math.max(8, Math.min(window.scrollX + found.rect.left, window.scrollX + document.documentElement.clientWidth - 280));
  bar.style.top = `${top}px`;
  bar.style.left = `${left}px`;
  const viewport = window.visualViewport;
  const keyboard = viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0;
  const tabs = $("tabbar").getBoundingClientRect();
  bar.style.setProperty("--selection-bottom", `${keyboard + (tabs.height || 8)}px`);
}

document.addEventListener("selectionchange", () => {
  // Delay so a click that clears the selection hides the bar after the new selection settles.
  clearTimeout(showSelectBar.timer);
  showSelectBar.timer = setTimeout(() => { showSelectBar(); flushDeferredRender(); }, 60);
});
document.addEventListener("scroll", showSelectBar, true);
window.visualViewport?.addEventListener("resize", showSelectBar);
window.visualViewport?.addEventListener("scroll", showSelectBar);


for (const node of document.querySelectorAll("[data-route]")) node.href = hashLink(node.dataset.route);

async function start() {
  try {
    app.csrf = (await api(UI.api.session)).csrf;
  } catch (_) {
    return; // showLogin already ran on 401
  }
  $("login").hidden = true;
  $("app").hidden = false;
  document.querySelector(".topbar").hidden = false;
  await refresh(true);
  startPolling();
}

// ---------------------------------------------------------------- file viewer (v0.9.8.8)
// A tapped path opens here: safe markdown (the same DOM-only renderer as agent text), the real path
// and when it changed, copy path, and a diff when the same text names an original and a draft.
// Read-only: one GET /api/file, nothing stored. The sheet lives outside #app, so a live refresh
// never rebuilds it; full screen on a phone, a large panel on the desktop.
const FILE_DRAFT = /draft|proposed|suggest|revis|edit|new|section|patch|v\d+$/i;

function fileStem(path) { return fileName(path).replace(/\.[^.]+$/, "").toLowerCase(); }

// The other file in the same text, when one reads as the original and the other as the draft:
// one name extends the other (working-styles -> working-styles-agents-section) or says draft.
function filePartner(chip) {
  const box = chip.closest(".md, .ask-item, .question-card, .msg, .output, .term-pre, li, p, .card") || chip.parentNode;
  const path = chip.dataset.file;
  const others = [...new Set([...(box?.querySelectorAll("[data-file]") || [])].map((c) => c.dataset.file))].filter((p) => p !== path);
  if (!others.length) return null;
  const me = fileStem(path);
  const related = others.find((p) => { const o = fileStem(p); return o !== me && (me.startsWith(o) || o.startsWith(me)); })
    || (others.length === 1 && (FILE_DRAFT.test(me) || FILE_DRAFT.test(fileStem(others[0]))) ? others[0] : null);
  if (!related) return null;
  const o = fileStem(related);
  const meDraft = me.startsWith(o) && me !== o ? true : o.startsWith(me) ? false : FILE_DRAFT.test(me);
  return { path: related, original: meDraft ? related : path, draft: meDraft ? path : related, meDraft };
}

function fileAgent(chip) {
  const own = chip.closest("[data-agent-identity]")?.dataset.agentIdentity;
  if (own) return own;
  if (chip.closest("#terminal") && typeof term !== "undefined" && term.open) return term.open.identity;
  const r = route();
  return r.name === "agent" ? r.id : "";
}

function openFile(chip) {
  app.fileView = { path: chip.dataset.file, identity: fileAgent(chip), partner: filePartner(chip), data: null, error: "",
    diff: null, showDiff: false, raw: false, status: "", from: chip };
  renderFileSheet();
  loadFile();
}

function closeFile() {
  const from = app.fileView?.from;
  app.fileView = null;
  $("file-sheet")?.remove();
  document.body.classList.remove("file-on");
  if (from?.isConnected) from.focus();
}

async function loadFile() {
  const v = app.fileView; if (!v) return;
  const q = new URLSearchParams({ path: v.path });
  if (v.identity) q.set("identity", v.identity);
  try { v.data = await api(`${UI.api.file}?${q}`); v.error = ""; }
  catch (err) { v.error = err.message || String(err); }
  if (app.fileView === v) renderFileSheet();
}

async function loadFileDiff() {
  const v = app.fileView; if (!v?.partner) return;
  const q = new URLSearchParams({ path: v.partner.draft, against: v.partner.original });
  if (v.identity) q.set("identity", v.identity);
  try { v.diff = await api(`${UI.api.file}?${q}`); }
  catch (err) { v.diff = { error: err.message || String(err) }; }
  if (app.fileView === v) renderFileSheet();
}

async function copyFilePath(input) {
  const v = app.fileView, L = UI.files.labels;
  try { await navigator.clipboard.writeText(input.value); v.status = L.copied; }
  catch (_) {
    // Plain http over the tailnet has no clipboard API: select it and try the old way.
    input.focus(); input.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    v.status = ok ? L.copied : L.copy_failed;
  }
  renderFileSheet();
}

function fileWhen(ts) {
  return new Date(ts * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

function fileDiffRows(d) {
  const L = UI.files.labels;
  if (d.error) return h("p", { class: "error file-error", text: d.error });
  if (!d.diff) return h("p", { class: "muted", text: L.loading });
  if (d.diff.same) return h("p", { class: "muted", text: L.diff_same });
  return h("div", { class: "file-diff", role: "table", "aria-label": `${d.against?.name} → ${d.name}` },
    h("p", { class: "muted small file-diff-sum", text: `${L.diff_against.replace("{name}", d.against?.shown || "")} · +${d.diff.added} −${d.diff.removed}` }),
    d.diff.rows.map((r) => r.op === "skip" ? h("div", { class: "fd-row fd-skip", role: "row", text: L.diff_skip.replace("{n}", r.n) })
      : h("div", { class: `fd-row fd-${r.op}`, role: "row" }, h("span", { class: "fd-op", "aria-hidden": "true", text: r.op === "add" ? "+" : r.op === "del" ? "−" : " " }),
        h("span", { class: "fd-text", text: r.text || " " }))));
}

function renderFileSheet() {
  const v = app.fileView; if (!v) return;
  const L = UI.files.labels, d = v.data;
  let root = $("file-sheet");
  const fresh = !root;
  if (fresh) { root = h("div", { id: "file-sheet" }); document.body.appendChild(root); document.body.classList.add("file-on"); }
  const scroll = root.querySelector(".file-body")?.scrollTop || 0;
  const focusKey = root.contains(document.activeElement) ? document.activeElement.getAttribute("data-fk") : null;
  const pathInput = d ? h("input", { class: "file-path", readonly: true, value: d.path, "aria-label": "Path", "data-reading-key": "file-path" }) : null;
  if (pathInput) pathInput.value = d.path;
  const p = v.partner;
  const diffLabel = p ? (p.meDraft ? L.diff_vs_original : L.diff_vs_draft) : null;
  const body = v.error ? h("div", { class: "file-missing" }, h("p", { class: "file-error", role: "alert", text: v.error.replace(/^\w/, (c) => c.toUpperCase()) }),
      h("p", { class: "muted small", text: v.path }))
    : !d ? h("p", { class: "muted", text: L.loading })
    : v.showDiff ? fileDiffRows(v.diff || {})
    : d.kind === "markdown" && !v.raw ? mdBlock(d.text, { cls: "file-md" })
    : h("pre", { class: "file-pre" }, d.text);
  root.replaceChildren(h("div", { class: "file-backdrop", on: { click: closeFile } }),
    h("div", { class: "file-panel", role: "dialog", "aria-modal": "true", "aria-labelledby": "file-title",
        on: { keydown: (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeFile(); } } } },
      h("div", { class: "file-head" },
        h("button", { class: "btn quiet file-close", "data-fk": "close", type: "button", "aria-label": L.close, text: "✕", on: { click: closeFile } }),
        h("div", { class: "file-titles" },
          h("h2", { id: "file-title", class: "file-title", text: d?.name || fileName(v.path) }),
          d ? h("p", { class: "muted small file-meta", text: [L.modified.replace("{when}", fileWhen(d.mtime)), `${Math.max(1, Math.round(d.size / 1024))} KB`].join(" · ") } ) : null)),
      d ? h("div", { class: "file-tools" }, pathInput,
        h("button", { class: "btn quiet", "data-fk": "copy", type: "button", text: L.copy, on: { click: () => copyFilePath(pathInput) } }),
        p ? h("button", { class: "btn quiet", "data-fk": "diff", type: "button", "aria-pressed": String(v.showDiff), text: diffLabel, title: `${fileName(p.original)} → ${fileName(p.draft)}`,
          on: { click: () => { v.showDiff = !v.showDiff; if (v.showDiff && !v.diff) loadFileDiff(); renderFileSheet(); } } }) : null,
        d.kind === "markdown" && !v.showDiff ? h("button", { class: "btn quiet", "data-fk": "plain", type: "button", "aria-pressed": String(v.raw), text: L.plain,
          on: { click: () => { v.raw = !v.raw; renderFileSheet(); } } }) : null,
        v.status ? h("span", { class: "muted small file-status", role: "status", text: v.status }) : null) : null,
      h("div", { class: "file-body", tabindex: "0" }, body),
      h("p", { class: "muted small file-foot", text: L.read_only })));
  const b = root.querySelector(".file-body"); if (b && !fresh) b.scrollTop = scroll;
  if (fresh || focusKey) root.querySelector(`[data-fk="${fresh ? "close" : focusKey}"]`)?.focus();
}

// One listener for every chip (cards, reports, conversation, terminal): capture, so a chip inside a
// row link or button opens the file and not the row.
document.addEventListener("click", (e) => {
  const chip = e.target.closest?.("[data-file]");
  if (!chip || chip.closest("#file-sheet .file-tools")) return;
  e.preventDefault(); e.stopPropagation();
  openFile(chip);
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && app.fileView) { e.preventDefault(); e.stopPropagation(); closeFile(); return; }
  const chip = e.target.closest?.("[data-file]");
  if (!chip || (e.key !== "Enter" && e.key !== " ")) return;
  e.preventDefault(); e.stopPropagation();
  openFile(chip);
}, true);
window.addEventListener("hashchange", () => { if (app.fileView) closeFile(); });

$("login-form").addEventListener("submit", login);
const hashKey = (hash = location.hash) => hash || hashLink('home');
history.scrollRestoration = "manual";               // we restore per route ourselves

window.addEventListener("hashchange", (event) => {
  app.scroll[hashKey(new URL(event.oldURL).hash)] = window.scrollY;   // restored when we come back
  if (app.trail[app.trail.length - 2] === hashKey()) app.trail.pop(); else app.trail.push(hashKey());
  app.chooser = route().name === "settings" ? null : app.chooser;
  if(app.composer?.kind==='reply') {saveDraft(replyDraftKey(app.composer),app.composer.text || '');app.composer=null;renderComposer();}
  render(true);
});
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && app.csrf) refresh(); });
start();

// ---------------------------------------------------------------- visuals hub, toasts and lightbox (v0.9.9)

function visualsSummary() {
  return app.view?.visuals || {unseen: 0, items: []};
}

function visualsEntry() {
  const n = visualsSummary().unseen || 0;
  const badge = n ? h("span", {class: "tab-badge visuals-badge", text: n > 99 ? "99+" : String(n)}) : h("span", {class: "tab-badge visuals-badge", hidden: true});
  return h("a", {class: "visuals-entry", href: hashLink("visuals"), "aria-label": n ? `Visuals, ${n} new` : "Visuals", title: "Visuals"},
    icon("visuals", "i"), badge);
}

function syncVisualsChrome() {
  const n = visualsSummary().unseen || 0;
  const label = n ? `Visuals, ${n} new` : "Visuals";
  const text = n > 99 ? "99+" : String(n);
  for (const el of document.querySelectorAll(".visuals-badge")) {
    if (!n) { el.hidden = true; el.textContent = ""; }
    else { el.hidden = false; el.textContent = text; }
  }
  for (const a of document.querySelectorAll("a.visuals-entry")) a.setAttribute("aria-label", label);
  maybeToastNewVisuals();
}

function maybeToastNewVisuals() {
  const items = visualsSummary().items || [];
  app.visualsKnown = app.visualsKnown || new Set();
  if (!app.visualsPrimed) {
    for (const item of items) app.visualsKnown.add(item.id);
    app.visualsPrimed = true;
    return;
  }
  const fresh = items.filter((item) => !item.seen && !app.visualsKnown.has(item.id));
  for (const item of fresh) app.visualsKnown.add(item.id);
  if (fresh.length) showVisualToasts(fresh);
}

function showVisualToasts(items) {
  const host = $("visual-toasts");
  if (!host) return;
  const show = items.length > 2
    ? [{batch: true, items, name: `${items.length} new visuals`, agent: items.map((i) => i.agent).filter((v, i, a) => a.indexOf(v) === i).slice(0, 3).join(", "), at: items[0].at, thumbnail: items[0].thumbnail}]
    : items;
  for (const item of show) {
    const toast = h("button", {class: "visual-toast", type: "button", on: {click: () => {
      toast.remove();
      if (item.batch) { location.hash = hashLink("visuals"); return; }
      openVisual(item, items);
    }}},
      item.video ? h("div", {class: "toast-thumb", text: "Video"}) : h("img", {class: "toast-thumb", src: item.thumbnail, alt: ""}),
      h("div", {}, h("div", {class: "toast-name", text: item.name}), h("div", {class: "toast-meta", text: `${item.agent || "Agent"} · ${ago(item.at)} ago`})),
      h("span", {class: "muted small", text: "View"}));
    host.append(toast);
    setTimeout(() => { toast.classList.add("leaving"); setTimeout(() => toast.remove(), 280); }, 7000);
  }
  while (host.children.length > 3) host.firstChild.remove();
}

function formatBytes(n) {
  const size = Number(n);
  if (!Number.isFinite(size) || size < 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function visualDayLabel(at) {
  const d = new Date(at * 1000);
  const today = new Date();
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, today)) return "Today";
  const y = new Date(today); y.setDate(y.getDate() - 1);
  if (same(d, y)) return "Yesterday";
  return d.toLocaleDateString([], {weekday: "short", day: "numeric", month: "short"});
}

async function markVisualsSeen(ids, all = false) {
  try {
    const result = await api(UI.api.visuals_seen, {method: "POST", body: all ? {all: true} : {ids}});
    if (app.view?.visuals) app.view.visuals.unseen = result.unseen;
    if (all && app.hubVisuals?.items) app.hubVisuals.items.forEach((i) => { i.seen = true; });
    else if (ids && app.hubVisuals?.items) app.hubVisuals.items.forEach((i) => { if (ids.includes(i.id)) i.seen = true; });
    syncVisualsChrome();
    return result;
  } catch (_) { return null; }
}

async function loadHubVisuals({reset = false} = {}) {
  const f = app.visualsFilter || {};
  const query = new URLSearchParams({hub: "1", limit: "24"});
  if (f.workspace) query.set("workspace", f.workspace);
  if (f.identity) query.set("identity", f.identity);
  if (!reset && app.hubVisuals?.next) query.set("before", String(app.hubVisuals.next));
  if (reset) query.set("refresh", "1");
  if (reset) app.hubVisuals = {items: [], loading: true, next: null};
  else if (app.hubVisuals) app.hubVisuals.loading = true;
  render(true);
  try {
    const data = await api(`${UI.api.visuals}?${query}`, {signal: AbortSignal.timeout(VISUALS_WAIT_MS)});
    const previous = reset ? [] : (app.hubVisuals?.items || []);
    const seen = new Set(previous.map((i) => i.id));
    const added = (data.items || []).filter((i) => !seen.has(i.id));
    app.hubVisuals = {items: previous.concat(added), loading: false, next: data.next, unseen: data.unseen,
      partial: data.partial, partial_reason: data.partial_reason, error: null};
    if (typeof data.unseen === "number" && app.view) {
      app.view.visuals = app.view.visuals || {};
      app.view.visuals.unseen = data.unseen;
    }
  } catch (err) {
    app.hubVisuals = {...(app.hubVisuals || {items: []}), loading: false,
      error: err.name === "TimeoutError" || err.name === "AbortError" ? VISUALS_SLOW : err.message};
  }
  render(true);
}

function renderVisualsHub(r) {
  if (!app.hubVisuals && !app.hubLoadingStarted) { app.hubLoadingStarted = true; loadHubVisuals({reset: true}); }
  if (app.visualsFilter == null) app.visualsFilter = {workspace: "", identity: ""};
  const data = app.hubVisuals || {items: [], loading: true};
  const workspaces = app.view?.workspaces || [];
  const agents = (app.view?.agents || []).filter((a) => !a.folded && a.cwd);
  const days = [];
  let last = "";
  for (const item of data.items || []) {
    const day = visualDayLabel(item.at);
    if (day !== last) { days.push({day, items: []}); last = day; }
    days.at(-1).items.push(item);
  }
  if (r?.id && data.items?.length && !app.viewer) {
    const index = data.items.findIndex((i) => i.id === r.id);
    if (index >= 0) setTimeout(() => openVisual(data.items[index], data.items), 0);
  }
  const onScroll = (event) => {
    if (data.loading || !data.next) return;
    const el = event.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 240) loadHubVisuals();
  };
  return h("div", {class: "visuals-hub", "data-reading-key": "visuals-hub", on: {scroll: onScroll}},
    h("a", {class: "back", href: hashLink("list"), text: "Back"}),
    h("div", {class: "hub-head"},
      h("h1", {text: "Visuals"}),
      h("div", {class: "actions"},
        h("button", {class: "btn quiet", type: "button", text: "Refresh", on: {click: () => loadHubVisuals({reset: true})}}),
        data.unseen ? h("button", {class: "btn quiet", type: "button", text: "Mark all seen", on: {click: async () => {
          await markVisualsSeen(null, true); if (app.hubVisuals) app.hubVisuals.unseen = 0; render(true);
        }}}) : null)),
    h("p", {class: "lede muted", text: data.unseen ? `${data.unseen} new` : "Recent screenshots, images and videos from your agents."}),
    h("div", {class: "hub-filters"},
      h("select", {"aria-label": "Workspace", value: app.visualsFilter.workspace || "", on: {change: (e) => {
        app.visualsFilter.workspace = e.target.value; app.visualsFilter.identity = ""; loadHubVisuals({reset: true});
      }}}, h("option", {value: "", text: "All workspaces"}),
        ...workspaces.map((w) => h("option", {value: w.id, text: w.label, selected: app.visualsFilter.workspace === w.id}))),
      h("select", {"aria-label": "Agent", value: app.visualsFilter.identity || "", on: {change: (e) => {
        app.visualsFilter.identity = e.target.value; loadHubVisuals({reset: true});
      }}}, h("option", {value: "", text: "All agents"}),
        ...agents.filter((a) => !app.visualsFilter.workspace || a.workspace_id === app.visualsFilter.workspace)
          .map((a) => h("option", {value: a.identity, text: displayName(a), selected: app.visualsFilter.identity === a.identity})))),
    data.error ? h("p", {class: "error", text: data.error}) : null,
    data.partial ? h("p", {class: "muted small", text: `Not all folders were scanned${data.partial_reason ? `: ${data.partial_reason}` : ""}.`}) : null,
    days.map((group) => [
      h("h2", {class: "visuals-day", text: group.day}),
      h("div", {class: "visuals-grid"}, group.items.map((item) =>
        h("button", {class: `visual-tile${item.seen ? "" : " is-unseen"}`, type: "button", on: {click: () => openVisual(item, data.items)}},
          item.video ? h("span", {class: "video-preview"}, visualThumb(item, "Video preview"), h("span", {text: "Play video"}))
            : visualThumb(item, item.name),
          h("span", {class: "visual-name", text: item.name}),
          h("span", {class: "muted small", text: `${item.agent} · ${ago(item.at)} ago`}))))
    ]),
    data.loading ? h("p", {class: "muted small", role: "status", text: "Looking for images and videos…"}) : null,
    !data.loading && !data.items?.length ? h("p", {class: "muted", text: "No recent images or videos in the last 48 hours."}) : null,
    !data.loading && data.next ? h("button", {class: "btn", type: "button", text: "Load earlier", on: {click: () => loadHubVisuals()}}) : null);
}


const THUMB_CONCURRENCY = 5;
const thumbQueue = [];
let thumbActive = 0;
let thumbObserver = null;

function visualThumb(item, alt) {
  return h("span", {class: "visual-thumb-wrap"},
    h("span", {class: "visual-skeleton", "aria-hidden": "true"},
      h("span", {class: "visual-skel-name", text: item.agent || "Visual"})),
    h("img", {alt: alt || item.name || "", loading: "lazy", decoding: "async", "data-thumb": item.thumbnail || ""}));
}

function queueVisualThumb(img) {
  if (!img || img.dataset.bound === "src" || img.dataset.bound === "queued") return;
  img.dataset.bound = "queued";
  thumbQueue.push(img);
  pumpVisualThumbs();
}

function pumpVisualThumbs() {
  while (thumbActive < THUMB_CONCURRENCY && thumbQueue.length) {
    const img = thumbQueue.shift();
    if (!img || !img.isConnected || img.dataset.bound === "src") continue;
    img.dataset.bound = "src";
    const src = img.getAttribute("data-thumb") || "";
    const done = () => { thumbActive = Math.max(0, thumbActive - 1); pumpVisualThumbs(); };
    img.addEventListener("load", () => {
      img.classList.add("is-loaded");
      const skel = img.parentElement && img.parentElement.querySelector(".visual-skeleton");
      if (skel) skel.remove();
      done();
    }, {once: true});
    img.addEventListener("error", () => {
      img.classList.add("is-failed");
      const wrap = img.closest(".visual-thumb-wrap");
      const skel = wrap && wrap.querySelector(".visual-skeleton");
      if (skel) {
        skel.classList.add("is-error");
        const label = skel.querySelector(".visual-skel-name");
        if (label) label.textContent = "Couldn't load";
      }
      done();
    }, {once: true});
    if (!src) { img.dispatchEvent(new Event("error")); continue; }
    thumbActive += 1;
    img.src = src;
  }
}

function bindVisualThumbs(root) {
  const imgs = [...(root || document).querySelectorAll("img[data-thumb]")].filter((img) => !img.dataset.bound);
  if (!imgs.length) return;
  if (typeof IntersectionObserver !== "function") {
    imgs.forEach(queueVisualThumb);
    return;
  }
  if (!thumbObserver) {
    thumbObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        thumbObserver.unobserve(entry.target);
        queueVisualThumb(entry.target);
      }
    }, {root: null, rootMargin: "200px 0px", threshold: 0.01});
  }
  for (const img of imgs) thumbObserver.observe(img);
}

function openVisual(item, list) {
  document.querySelector("#visual-viewer")?.remove();
  const items = (list && list.length ? list : [item]).slice();
  let index = Math.max(0, items.findIndex((i) => i.id === item.id));
  if (index < 0) index = 0;
  let scale = 1, tx = 0, ty = 0;
  const pointers = new Map();
  let dragging = false, dragX = 0, dragY = 0, startTx = 0, startTy = 0, moved = false;
  let pinch0 = 0, pinchScale = 1;
  let lastTap = 0, lastTapX = 0, lastTapY = 0;
  const state = {index};
  const close = () => {
    dialog.close();
    if (route().name === "visuals" && route().id) history.replaceState(null, "", hashLink("visuals"));
  };
  const mediaEl = () => stage.querySelector("img, video");
  const limits = () => {
    const media = mediaEl();
    if (!media || scale <= 1) return {maxX: 0, maxY: 0};
    const maxX = Math.max(0, (media.offsetWidth * scale - stage.clientWidth) / 2);
    const maxY = Math.max(0, (media.offsetHeight * scale - stage.clientHeight) / 2);
    return {maxX, maxY};
  };
  const clamp = () => {
    if (scale <= 1) { scale = 1; tx = 0; ty = 0; return; }
    const {maxX, maxY} = limits();
    tx = Math.min(maxX, Math.max(-maxX, tx));
    ty = Math.min(maxY, Math.max(-maxY, ty));
  };
  const applyZoom = () => {
    if (scale <= 1) { scale = 1; tx = 0; ty = 0; }
    else clamp();
    const media = mediaEl();
    if (media) media.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  };
  const zoomAt = (clientX, clientY, nextScale) => {
    const box = stage.getBoundingClientRect();
    const px = clientX - (box.left + box.width / 2);
    const py = clientY - (box.top + box.height / 2);
    const old = scale || 1;
    const target = Math.min(4, Math.max(1, nextScale));
    if (target === 1) { scale = 1; tx = 0; ty = 0; applyZoom(); return; }
    const ix = (px - tx) / old, iy = (py - ty) / old;
    scale = target;
    tx = px - ix * scale;
    ty = py - iy * scale;
    applyZoom();
  };
  const preloadNeighbors = () => {
    if (items.length < 2) return;
    for (const step of [-1, 1]) {
      const neighbor = items[(state.index + step + items.length) % items.length];
      if (neighbor?.thumbnail) { const im = new Image(); im.decoding = "async"; im.src = neighbor.thumbnail; }
      if (neighbor?.url && !neighbor.video) { const im = new Image(); im.decoding = "async"; im.src = neighbor.url; }
    }
  };
  const show = (next) => {
    state.index = (next + items.length) % items.length;
    scale = 1; tx = 0; ty = 0;
    fill();
    const current = items[state.index];
    if (current && !current.seen) markVisualsSeen([current.id]);
  };
  const fill = () => {
    const current = items[state.index];
    if (!current) return;
    title.textContent = current.name;
    let media;
    if (current.video) {
      media = h("video", {src: current.url, controls: true, playsinline: true, preload: "metadata"});
    } else {
      media = h("img", {src: current.thumbnail || current.url, alt: current.name, draggable: false, decoding: "async"});
      if (current.url && current.thumbnail && current.url !== current.thumbnail) {
        const hi = new Image();
        hi.onload = () => {
          if (items[state.index] !== current) return;
          media.src = current.url;
        };
        hi.src = current.url;
      }
    }
    stage.replaceChildren(media);
    applyZoom();
    const parts = [
      current.agent,
      current.workspace_label,
      current.pane_id,
      clock(current.at),
      formatBytes(current.size),
      current.relative_path || current.path,
    ].filter(Boolean);
    meta.textContent = parts.join(" · ");
    goTo.href = hashLink("agent", current.identity);
    download.href = current.download || current.url;
    download.setAttribute("download", current.name || "visual");
    prev.hidden = items.length < 2;
    next.hidden = items.length < 2;
    dialog.setAttribute("aria-label", current.name);
    preloadNeighbors();
  };
  const title = h("b");
  const stage = h("div", {class: "visual-stage"});
  const meta = h("p", {class: "muted small visual-meta"});
  const prev = h("button", {class: "visual-nav prev", type: "button", text: "‹", "aria-label": "Previous", on: {click: () => show(state.index - 1)}});
  const next = h("button", {class: "visual-nav next", type: "button", text: "›", "aria-label": "Next", on: {click: () => show(state.index + 1)}});
  const download = h("a", {class: "btn", text: "Download"});
  const goTo = h("a", {class: "btn primary", text: "Go to agent", on: {click: close}});
  const dialog = h("dialog", {id: "visual-viewer", class: "visual-viewer is-lightbox", on: {
    close: () => { document.removeEventListener("keydown", onKey); dialog.remove(); app.viewer = null; },
  }},
    h("div", {class: "visual-viewer-head"}, title, h("button", {class: "btn", type: "button", text: "Close", on: {click: close}})),
    h("div", {style: {position: "relative", flex: "1", minHeight: "0", display: "flex", flexDirection: "column"}}, stage, prev, next),
    meta,
    h("div", {class: "visual-actions actions"},
      items[0] && !items[state.index]?.video ? h("button", {class: "btn quiet", type: "button", text: "Zoom", on: {click: () => {
        const box = stage.getBoundingClientRect();
        if (scale > 1) zoomAt(box.left + box.width / 2, box.top + box.height / 2, 1);
        else zoomAt(box.left + box.width / 2, box.top + box.height / 2, 2);
      }}}) : null,
      download, goTo,
      h("button", {class: "btn quiet", type: "button", text: "Reply about this", on: {click: () => {
        const current = items[state.index]; close(); draftCoordinator(current.identity, `About ${current.path}:\n`);
      }}})));
  const onKey = (event) => {
    if (!dialog.open) return;
    if (event.key === "ArrowRight") { event.preventDefault(); show(state.index + 1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); show(state.index - 1); }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); zoomAt(stage.getBoundingClientRect().left + stage.clientWidth / 2, stage.getBoundingClientRect().top + stage.clientHeight / 2, scale + 0.4); }
    if (event.key === "-" || event.key === "_") { event.preventDefault(); zoomAt(stage.getBoundingClientRect().left + stage.clientWidth / 2, stage.getBoundingClientRect().top + stage.clientHeight / 2, scale - 0.4); }
    if (event.key === "0") { event.preventDefault(); scale = 1; tx = 0; ty = 0; applyZoom(); }
    if (event.key === "Escape") close();
  };
  stage.addEventListener("wheel", (event) => {
    if (items[state.index]?.video) return;
    event.preventDefault();
    zoomAt(event.clientX, event.clientY, scale + (event.deltaY < 0 ? 0.2 : -0.2));
  }, {passive: false});
  stage.addEventListener("pointerdown", (event) => {
    if (items[state.index]?.video) return;
    if (event.button != null && event.button !== 0) return;
    stage.setPointerCapture?.(event.pointerId);
    pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    moved = false;
    if (pointers.size === 1) {
      dragging = true; dragX = event.clientX; dragY = event.clientY; startTx = tx; startTy = ty;
    } else if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch0 = Math.hypot(a.x - b.x, a.y - b.y);
      pinchScale = scale;
    }
  });
  stage.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    if (pointers.size === 2 && pinch0) {
      event.preventDefault();
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y) || pinch0;
      zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, pinchScale * (dist / pinch0));
      moved = true;
      return;
    }
    if (pointers.size === 1 && dragging) {
      const dx = event.clientX - dragX, dy = event.clientY - dragY;
      if (Math.hypot(dx, dy) > 8) moved = true;
      if (scale > 1) {
        event.preventDefault();
        stage.classList.add("is-panning");
        tx = startTx + dx; ty = startTy + dy;
        applyZoom();
      }
    }
  });
  const pointerEnd = (event) => {
    const start = pointers.get(event.pointerId);
    pointers.delete(event.pointerId);
    if (pointers.size < 2) pinch0 = 0;
    if (pointers.size) return;
    stage.classList.remove("is-panning");
    const dx = event.clientX - dragX, dy = event.clientY - dragY;
    const now = Date.now();
    if (!moved && now - lastTap < 320 && Math.hypot(event.clientX - lastTapX, event.clientY - lastTapY) < 28) {
      if (scale > 1) zoomAt(event.clientX, event.clientY, 1);
      else zoomAt(event.clientX, event.clientY, 2);
      lastTap = 0;
    } else if (!moved) {
      lastTap = now; lastTapX = event.clientX; lastTapY = event.clientY;
    } else if (scale <= 1 && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
      show(state.index + (dx < 0 ? 1 : -1));
    } else if (scale > 1 && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
      const {maxX} = limits();
      if (dx < 0 && Math.abs(tx + maxX) < 2) show(state.index + 1);
      else if (dx > 0 && Math.abs(tx - maxX) < 2) show(state.index - 1);
    }
    dragging = false;
  };
  stage.addEventListener("pointerup", pointerEnd);
  stage.addEventListener("pointercancel", pointerEnd);
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", close, {once: true});
  document.body.append(dialog);
  dialog.showModal();
  app.viewer = state;
  fill();
  const current = items[state.index];
  if (current && !current.seen) markVisualsSeen([current.id]);
}


function maybeLoadMoreVisuals() {
  if (route().name !== "visuals" || !app.hubVisuals || app.hubVisuals.loading || !app.hubVisuals.next) return;
  if (document.documentElement.scrollHeight - window.scrollY - window.innerHeight < 280) loadHubVisuals();
}
window.addEventListener("scroll", maybeLoadMoreVisuals, {passive: true});
