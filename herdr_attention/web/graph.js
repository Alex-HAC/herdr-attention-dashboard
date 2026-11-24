// Graph shell: the graph IS the interface. One shared space, workspaces as
// regions, agents as nodes; light overlays (tool palette, zoom buttons) and
// one contextual sheet (inspector or a panel) on top of the canvas.
//
// Stability rules (why it no longer jumps around):
//  * one persistent <svg>; every refresh updates elements in place by id
//  * node positions are cached (relative to their region) and only new agents
//    are placed; sizes use an absolute scale, so one agent's growth never
//    resizes the others; a region grows with its agents and shrinks again
//    when they go (v0.9.8.6: a closed fold of 600 subagents left it huge)
//  * the viewport (pan/zoom) is never reset by data, panels or selection;
//    fit-to-view happens once on first load, or when you press Fit
//  * gestures keep their own baseline; when a finger lifts, the remaining one
//    continues from the CURRENT transform (the old code reused the transform
//    from before the pinch, which made pinch zoom "shoot back")
// Uses helpers from app.js (h, link, displayName, api, ...).
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const ZOOM_MIN = 0.2;
const ZOOM_MAX = 4;
const TAP_SLOP = { touch: 10, pen: 6, mouse: 4 };

function graphStateForView(options = {}) { return {
  size: "context", transform: null, selected: null, panel: null, sheetExpanded: false,
  hiddenWorkspaces: new Set(), statusFilter: new Set(), hideSubagents:false, expandedSubagents:new Set(), conversation: {},
  svg: null, layers: null, nodes: {}, regions: {}, edges: {}, hiddenEnds: {}, els: { nodes: {}, regions: {}, edges: {}, hidden: {}, labels: {}, flows: {} },
  pointers: new Map(), gesture: null, tap: null, interacting: false, userMoved: false, shell: null,
  inspectorKey: "", panelKey: "", ...options,
}; }
let graph = graphStateForView();

function inGraph(state, fn) {
  const previous = graph;
  try { graph = state; return fn(); } finally { graph = previous; }
}

try {
  const saved = JSON.parse(sessionStorage.getItem("hat.graph") || "null");
  if (saved && saved.transform) { graph.transform = saved.transform; graph.restored = true; }
  if (saved && saved.size) graph.size = saved.size;
} catch (_) { /* storage unavailable: start fresh */ }

function saveGraphState() {
  try { sessionStorage.setItem("hat.graph", JSON.stringify({ transform: graph.transform, size: graph.size })); } catch (_) { /* ignore */ }
}

function s(tag, attrs, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  setAttrs(node, attrs);
  for (const child of children.flat(Infinity)) if (child) node.appendChild(child);
  return node;
}

function setAttrs(node, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "text") { if (node.textContent !== String(value)) node.textContent = String(value); continue; }
    if (value === undefined || value === null || value === false) { node.removeAttribute(key); continue; }
    const v = String(value);
    if (node.getAttribute(key) !== v) node.setAttribute(key, v);
  }
}

// ---------------------------------------------------------------- formatting

function tokens(n) {
  if (n === null || n === undefined) return "?";
  if (n < 1000) return `${n}`;
  if (n < 1e6) return `${(n / 1000).toFixed(n < 1e4 ? 1 : 0)}k`;
  if (n < 1e9) return `${(n / 1e6).toFixed(n < 1e7 ? 2 : 1)}M`;
  return `${(n / 1e9).toFixed(n < 1e10 ? 2 : 1)}B`;   // 1141.7M reads as 1.14B
}

function hours(seconds) {
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ${String(m % 60).padStart(2, "0")} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

const KIND_WORDS = { spawned_by: "spawned", forked_from: "forked", reports_to: "reports to" };

function roleText(agent) {
  const roles = (agent.roles && agent.roles.role) || [];
  return roles.map((r) => r.value).join(", ");
}

// Herdr status of an agent (what Herdr's own colours describe).
function herdrStatus(agent) {
  if (!agent.present) return agent.kind === "failure" ? "blocked" : "unknown";
  return ["working", "blocked", "done", "idle", "unknown"].includes(agent.status) ? agent.status : "unknown";
}

// Dashboard state the graph draws: the same states, names and order as the
// list. Red "need you" only when the agent's tier is waiting_on_you; a blocked
// agent that is waiting on someone else is drawn as waiting.
function graphState(agent) {
  if (agentTier(agent) === "waiting_on_you") return "needs_you";
  if (agent.state === "needs_you") return "waiting";
  return STATES.includes(agent.state) ? agent.state : "unknown";
}

const STATE_GLYPH = { out_of_quota: "!", needs_you: "?", working: "◐", done: "✓", waiting: "○", parked: "–", unknown: "·" };
const STATE_SAYS = { needs_you: "waiting on you", working: "working", done: "done, not reviewed",
  waiting: "waiting", parked: "parked", unknown: "unclear" };

function statusSymbol(agent) {
  if (agent.kind === "failure") return "!";
  return STATE_GLYPH[graphState(agent)] || "·";
}

// ---------------------------------------------------------------- what an agent runs on

// Service, model and effort come from the server (agent.runtime, agent_runtime.py): recorded
// values only, each with its source. Nothing is drawn for a value that is not recorded.
// Names, the effort scale and the wording: UI.runtime (ui_contract.json).
const RT = UI.runtime.labels;
const PIP = "\u2022";

function rtLabel(key, values = {}) {
  return (RT[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? ""));
}

function effortDots(e) {
  return e && Number.isInteger(e.level) ? { on: e.level, off: Math.max(0, e.steps - e.level) } : null;
}

function effortSays(e) {
  if (!e) return "";
  return effortDots(e) ? rtLabel("effort_steps", { word: e.label, n: e.level, steps: e.steps }) : rtLabel("effort_word", { word: e.label });
}

// One phrase for the tooltip and the screen reader: "Claude Code, Opus 5.5, effort extra high, 4 of 5".
function runtimeSays(agent) {
  const rt = agent.runtime || {};
  return [serviceSays(agent), rt.model?.label, effortSays(rt.effort)].filter(Boolean).join(", ");
}

// "Codex (Codex Work)": the tool, and the subscription when its account is recorded (v0.9.7).
function serviceSays(agent) {
  const tool = agent.runtime?.service?.label, account = agent.icon?.label;
  return tool && account ? `${tool} (${account})` : tool || account || "";
}

// v0.9.7: the account mark for a compact slot (graph node). Only a mark or an image, never the text
// label; an agent without a recorded account has none. Drawn by accountIcon() (app.js), the one renderer.
function compactAccountIcon(agent) {
  const icon = agent.icon;
  return icon && (icon.image || icon.glyph) ? icon : null;
}

// Default role of a spawned agent: the node shows only roles that stand out (coordinator, reviewer, ...).
function nodeRoleText(agent) {
  const roles = (agent.roles && agent.roles.role) || [];
  return roles.map((r) => r.value).filter((v) => v && v.toLowerCase() !== "worker").join(", ");
}

// The quiet second line under a node's name: the short model name and the effort dots.
function runtimeLine(agent, limit) {
  const rt = agent.runtime || {};
  const model = rt.model ? shortenLabel(rt.badge?.from === "model" ? rt.badge.text : rt.model.label, limit) : "";
  const dots = effortDots(rt.effort);
  const word = rt.effort && !dots ? rt.effort.label : "";
  if (!model && !dots && !word) return null;
  return [["nl-meta", [model, word].filter(Boolean).join(" \u00b7 ")],
    ["nl-pips on", dots ? (model ? " " : "") + PIP.repeat(dots.on) : ""],
    ["nl-pips off", dots ? (model && !dots.on ? " " : "") + PIP.repeat(dots.off) : ""]].filter(([, text]) => text);
}

// Name (line 1) and model and effort (line 2). The text is rebuilt only when it changes; placement
// shows or hides line 2 with the no-meta class (zoom and pan never rebuild it).
function setNodeLabel(el, agent, limit, withMeta) {
  const name = shortenLabel(displayName(agent), limit);
  const meta = runtimeLine(agent, Math.max(8, limit - 4));
  const key = JSON.stringify([name, meta]);
  if (el.dataset.key !== key) {
    el.dataset.key = key;
    el.replaceChildren(s("tspan", { class: "nl-name", text: name }),
      ...(meta || []).map(([cls, text], i) => s("tspan", i ? { class: cls, text } : { class: `${cls} nl-line2`, dy: "1.3em", text })));
  }
  el.classList.toggle("no-meta", !(withMeta && meta));
}

function placeText(el, x, y, anchor) {
  setAttrs(el, { x, y, "text-anchor": anchor });
  const line2 = el.querySelector(".nl-line2");
  if (line2) setAttrs(line2, { x });
}

// Hover text on a node: state; service, model (with its recorded id) and effort; context size.
function nodeTooltip(agent, state) {
  const rt = agent.runtime || {};
  const model = rt.model ? rt.model.label + (rt.model.id !== rt.model.label ? ` (${rt.model.id})` : "") : "";
  const runs = [serviceSays(agent), model, effortSays(rt.effort)].filter(Boolean).join(", ");
  const c = agent.metrics && agent.metrics.context;
  const context = c && c.available ? `context ${tokens(c.value)} tokens${c.window ? ` of ${tokens(c.window)}` : ""}` : "";
  return [`${displayName(agent)}: ${STATE_SAYS[state]}`, waitingSays(agent), runs, context].filter(Boolean).join(" \u00b7 ");
}

// ---------------------------------------------------------------- waiting and motion
//
// agent.waiting_for (flow.py): a quiet agent that is not stalled. shell = a command or background
// task is running, scheduled = a sleep or a wake-up it set itself, worker = it waits for a worker.
// Each has its own ring: shell turns slowly (dashes), scheduled ticks once a second (one dot),
// worker stays still while dots flow along the line to the busy worker. Working breathes.
// Stalled and needs-you stay still. prefers-reduced-motion stops all of it (style.css).

const WAIT_SAYS = { shell: "waiting for output", scheduled: "scheduled", worker: "waiting for a worker" };
const REDUCED_MOTION = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

function waitingSays(agent) {
  const w = agent.waiting_for;
  if (!w) return "";
  if (w.due_at) return `${w.label}, ${w.due_at > now() ? `due in ${duration(w.due_at - now())}` : "due now"}`;
  return w.since ? `${w.label}, for ${duration(now() - w.since)}` : w.label;
}

// The motion class of a node: what its ring does.
function motionOf(agent, state) {
  if (agent.waiting_for && state !== "needs_you") return `m-${agent.waiting_for.kind}`;
  if (state === "working" && agent.kind !== "stall") return "m-working";
  return "";
}

// A link's last action (flow.py): who sent what, when. Fresh actions are bright, older ones fade.
const ACTION_SAYS = { task: "sent task", report: "reported back", finished: "finished its turn" };

function actionAge(action) { return Math.max(0, now() - action.at); }

function actionFreshness(action) {
  const age = actionAge(action);
  return age < 120 ? "age-fresh" : age < 900 ? "age-recent" : age < 7200 ? "age-old" : "";
}

function actionSays(link) {
  const a = link.last_action;
  const parts = [];
  if (a) {
    const who = a.from === link.from ? link.from_name : link.to_name;
    parts.push(`${who ? `${who} ` : ""}${ACTION_SAYS[a.kind] || a.kind} ${duration(actionAge(a))} ago`);
    if (a.checked_at && a.checked_at > a.at) parts.push(`output checked ${duration(now() - a.checked_at)} ago`);
  }
  if (link.waiting) parts.push("coordinator is waiting for this worker");
  return parts.join(" \u00b7 ");
}

// Point and direction on the quadratic curve a -> b (control c) at t.
function curvePoint(a, c, b, t) {
  const u = 1 - t;
  return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y,
    angle: Math.atan2(2 * u * (c.y - a.y) + 2 * t * (b.y - c.y), 2 * u * (c.x - a.x) + 2 * t * (b.x - c.x)) * 180 / Math.PI };
}

function curveD(a, c, b) {
  return `M${a.x.toFixed(1)},${a.y.toFixed(1)} Q${c.x.toFixed(1)},${c.y.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`;
}

// One overlay per link: a chevron mid-line pointing from sender to receiver, a dot that travels
// that way while the action is fresh, and flowing dashes toward a worker its coordinator waits for.
function drawFlow(l, id, ends, c, dim) {
  const action = l.last_action, fresh = action ? actionFreshness(action) : "";
  if (!fresh && !l.waiting) return false;
  const g = keyed(graph.els.flows, graph.layers.flows, id, () => s("g", { class: "flow", "aria-hidden": "true" },
    s("path", { class: "wait-flow" }), s("path", { class: "chevron", d: "M-6,-5.5 L5,0 L-6,5.5 z" }),
    s("circle", { class: "courier", r: "3" })));
  const [wait, chevron, courier] = g.children;
  setAttrs(g, { class: ["flow", action ? `fx-${action.kind}` : "", fresh, dim ? "dim" : ""].filter(Boolean).join(" ") });
  // The waiting dashes run from the coordinator (l.to) to the worker (l.from).
  const [boss, worker] = [ends[l.to], ends[l.from]];
  setAttrs(wait, { d: l.waiting ? curveD(boss, c, worker) : null, visibility: l.waiting ? "visible" : "hidden" });
  if (!fresh) {
    setAttrs(chevron, { visibility: "hidden" });
    setAttrs(courier, { visibility: "hidden" });
    return true;
  }
  const [from, to] = [ends[action.from], ends[action.to]];
  const mid = curvePoint(from, c, to, 0.5);
  setAttrs(chevron, { visibility: "visible", transform: `translate(${mid.x.toFixed(1)} ${mid.y.toFixed(1)}) rotate(${mid.angle.toFixed(0)}) scale(${labelScale().toFixed(2)})` });
  // The courier: one trip, then a pause, repeated while fresh. SMIL, so no ids and no script per frame.
  const travel = fresh === "age-fresh" && !REDUCED_MOTION.matches;
  setAttrs(courier, { visibility: travel ? "visible" : "hidden" });
  let motion = courier.firstChild;
  if (!travel) { if (motion) motion.remove(); return true; }
  const d = curveD(from, c, to);
  if (!motion) {
    motion = s("animateMotion", { dur: "3.2s", repeatCount: "indefinite", calcMode: "linear", keyPoints: "0;1;1", keyTimes: "0;0.55;1" });
    courier.appendChild(motion);
  }
  setAttrs(motion, { path: d });
  return true;
}

// ---------------------------------------------------------------- sizes

// Absolute log scales: the same value always gives the same size.
const SIZE_SCALE = {
  context: { a: 7, ref: [10e3, 100e3, 1e6], label: "context tokens" },
  tokens: { a: 4.6, ref: [1e6, 10e6, 100e6], label: "session tokens" },
};

function metricValue(agent, mode) {
  const m = agent.metrics || {};
  if (mode === "context") return m.context && m.context.available ? m.context.value : null;
  if (mode === "tokens") return m.tokens && m.tokens.available ? m.tokens.value : null;
  return 1;
}

function radiusFor(value, mode) {
  if (mode === "equal") return 16;
  if (value === null || value === undefined) return 12;   // no data: small, dashed, still tappable
  return 8 + SIZE_SCALE[mode].a * Math.log10(1 + value / 1000);
}

// ---------------------------------------------------------------- layout

const GAP = 56;   // room for the name below and the role above each node

function regionPadding() { return 95; }
const LABEL_BAND_MAX = 16;   // room kept between regions for the label band on top of each circle

function findFreeSpot(region, r, members) {
  for (let i = 0; i < 400; i++) {
    const angle = i * 2.39996, dist = i === 0 ? 0 : 30 * Math.sqrt(i + 0.3);
    const x = Math.cos(angle) * dist, y = Math.sin(angle) * dist;
    if (members.every((m) => Math.hypot(m.x - x, m.y - y) >= m.r + r + GAP)) return { x, y };
  }
  return { x: region.R + r, y: 0 };
}

function placeRegion(ws, R) {
  // New region: to the right of the existing ones, wrapping into new rows at
  // a width that suits the screen's shape. Existing regions never move.
  const others = Object.values(graph.regions);
  const aspect = window.innerWidth >= 900 ? 1.3 : Math.max(0.45, window.innerWidth / Math.max(400, window.innerHeight));
  const totalArea = others.reduce((sum, o) => sum + (2 * o.R + 40) ** 2, 0) + (2 * R + 40) ** 2;
  const rowWidth = Math.max(2 * R + 40, Math.sqrt(totalArea * aspect) * 1.2);
  for (let y = R + 30; y < 20000; y += 40) {
    for (let x = R + 20; x + R <= Math.max(rowWidth, 2 * R + 40); x += 40) {
      if (others.every((o) => Math.hypot(o.cx - x, o.cy - y) >= o.R + R + 140 + LABEL_BAND_MAX)) return { cx: x, cy: y };
    }
  }
  return { cx: 0, cy: 0 };
}

function updateLayout(view) {
  const workspaces = activeWorkspaces(view);
  const watched = new Set(workspaces.map((w) => w.id));
  for (const id of Object.keys(graph.regions)) if (!watched.has(id)) delete graph.regions[id];
  const present = new Set();
  for (const ws of workspaces) {
    const members = view.agents.filter((a) => a.present && a.workspace_id === ws.id)
      .sort((x, y) => x.identity.localeCompare(y.identity));   // stable, never by state
    let region = graph.regions[ws.id];
    const placed = [];
    for (const a of members) {
      present.add(a.identity);
      const r = Math.max(radiusFor(metricValue(a, graph.size), graph.size), a.state === "parked" ? 12 : 0);
      let node = graph.nodes[a.identity];
      if (!node || node.ws !== ws.id) {
        const spot = findFreeSpot(region || { R: 80 }, r, [...new Set([...placed, ...Object.values(graph.nodes).filter(n => n.ws === ws.id)])]);
        node = { ws: ws.id, x: spot.x, y: spot.y, r };
        graph.nodes[a.identity] = node;
      }
      node.r = r;
      node.agent = a;
      placed.push(node);
    }
    // Sizes can change (metric switch, growth): push overlapping pairs apart
    // a little, for a bounded number of passes, then stop.
    for (let pass = 0; pass < 12; pass++) {
      let moved = false;
      for (let i = 0; i < placed.length; i++) for (let j = i + 1; j < placed.length; j++) {
        const p = placed[i], q = placed[j];
        const dx = q.x - p.x, dy = q.y - p.y, dist = Math.hypot(dx, dy) || 0.01, need = p.r + q.r + GAP;
        if (dist < need - 0.5) {
          const push = (need - dist) / 2, ux = dist < 0.02 ? 1 : dx / dist, uy = dist < 0.02 ? 0 : dy / dist;
          p.x -= ux * push; p.y -= uy * push; q.x += ux * push; q.y += uy * push;
          moved = true;
        }
      }
      if (!moved) break;
    }
    // v0.9.8.6: many nodes left at once (a fold closed, subagents hidden): the ones that stay are
    // packed again around the centre, nearest first, so the region can shrink back. A single agent
    // leaving keeps everyone where they are.
    if (region && region.count && placed.length && placed.length <= region.count * 0.75) {
      const repacked = [];
      for (const node of placed.slice().sort((p, q) => Math.hypot(p.x, p.y) - Math.hypot(q.x, q.y))) {
        const spot = findFreeSpot({ R: 80 }, node.r, repacked);
        node.x = spot.x; node.y = spot.y;
        repacked.push(node);
      }
    }
    const needR = Math.max(70, ...placed.map((p) => Math.hypot(p.x, p.y) + p.r + regionPadding()));
    if (!region) {
      region = { ...placeRegion(ws, needR), R: needR };
      graph.regions[ws.id] = region;
    } else if (needR > region.R) {
      region.R = needR;
      // Grew into a neighbour? Move only this region, along x, until free.
      const others = Object.entries(graph.regions).filter(([id]) => id !== ws.id).map(([, o]) => o);
      while (others.some((o) => Math.hypot(o.cx - region.cx, o.cy - region.cy) < o.R + region.R + 30 + LABEL_BAND_MAX)) region.cx += 40;
    } else if (needR < region.R - 0.5) {
      region.R = needR;   // shrinks in place; neighbours never move
    }
    region.count = placed.length;
    region.ws = ws;
  }
  for (const id of Object.keys(graph.nodes)) if (!present.has(id)) delete graph.nodes[id];
  // Placeholders for hidden / gone link ends sit just outside the region of the agent they belong to.
  graph.hiddenEnds = {};
  for (const l of view.links || []) {
    if ((l.label || "").startsWith("gone:")) continue;
    const placeholder = l.hidden ? l.to : l.from_hidden ? l.from : null;
    const anchorId = l.hidden ? l.from : l.to;
    const anchor = graph.nodes[anchorId];
    if (!placeholder || !anchor) continue;
    const region = graph.regions[anchor.ws];
    const d = Math.hypot(anchor.x, anchor.y) || 1;
    graph.hiddenEnds[placeholder] = { x: region.cx + (anchor.x / d) * (region.R + 28), y: region.cy + (anchor.y / d) * (region.R + 28), label: l.label };
  }
}

function worldPos(identity) {
  const n = graph.nodes[identity];
  if (!n) return null;
  const region = graph.regions[n.ws];
  return { x: region.cx + n.x, y: region.cy + n.y, r: n.r };
}

function bounds() {
  const rs = Object.values(graph.regions);
  if (!rs.length) return { x: 0, y: 0, w: 400, h: 300 };
  const x0 = Math.min(...rs.map((r) => r.cx - r.R)) - 20, y0 = Math.min(...rs.map((r) => r.cy - r.R - (r.band || 0))) - 12;
  const x1 = Math.max(...rs.map((r) => r.cx + r.R)) + 20, y1 = Math.max(...rs.map((r) => r.cy + r.R)) + 30;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

// ---------------------------------------------------------------- drawing (in place)

function ensureSvg() {
  if (graph.svg) return graph.svg;
  const svg = s("svg", { class: "graph", role: "group", "aria-label": "Agents by workspace. Use the list view for a text version." },
    s("defs", {}, s("marker", { id: graph.markerId || "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      s("path", { d: "M0,0 L10,5 L0,10 z", class: "arrowhead" }))));
  const viewport = s("g", { class: "viewport" });
  graph.layers = { regions: s("g", {}), edges: s("g", {}), flows: s("g", { class: "flows" }), hidden: s("g", {}), nodes: s("g", {}), labels: s("g", { class: "edge-labels" }) };
  viewport.append(graph.layers.regions, graph.layers.edges, graph.layers.flows, graph.layers.hidden, graph.layers.labels, graph.layers.nodes);
  svg.appendChild(viewport);
  graph.svg = svg;
  graph.viewport = viewport;
  if (graph.mini) return svg;
  attachGestures(svg);
  // Rotation / window resize: refit only while nobody has touched the view
  // (still the automatic first fit); otherwise the viewport is kept as is.
  new ResizeObserver(() => { if (graph.autoFit && !graph.userMoved) autoFit(); else applyTransform(); }).observe(svg);
  return svg;
}

function workspaceGraph(view, workspaceId) {
  const mini = graphStateForView({ mini: true, size: graph.size, markerId: `arrow-${workspaceId}`,
    transform: { k: 1, x: 0, y: 0 } });
  const agents = view.agents.filter(a => a.present && !a.folded && a.workspace_id === workspaceId);
  const ids = new Set(agents.map(a => a.identity));
  const regionView = { ...view, workspaces: view.workspaces.filter(w => w.id === workspaceId), agents,
    links: (view.links || []).filter(l => ids.has(l.from) && ids.has(l.to)) };
  const svg = inGraph(mini, () => { drawGraph(regionView); return graph.svg; });
  svg.setAttribute("aria-label", "Workspace agents and reporting lines");
  svg.addEventListener("click", e => { const id = e.target.closest(".node")?.dataset.id; if (id) selectNode(id); });
  // v0.9.8: double-click (double-tap) a node for its live terminal.
  svg.addEventListener("dblclick", e => { const id = e.target.closest(".node")?.dataset.id; if (id && typeof openTerminal === "function") { e.preventDefault(); openTerminal(id); } });
  const fit = () => inGraph(mini, () => {
    if (!svg.isConnected) return;
    const rect = svg.getBoundingClientRect(), b = bounds();
    const k = Math.min((rect.width - 24) / b.w, (rect.height - 24) / b.h, 1.2);
    graph.transform = { k, x: (rect.width - b.w * k) / 2 - b.x * k, y: (rect.height - b.h * k) / 2 - b.y * k };
    applyTransform();
  });
  // Observe only the mounted miniature; never mutate the main graph viewport.
  const observer = new ResizeObserver(() => { if (svg.isConnected) fit(); else observer.disconnect(); });
  requestAnimationFrame(() => { fit(); if (svg.isConnected) observer.observe(svg); });
  return h("section", { class: "card workspace-graph", "data-reading-key": `mini:${workspaceId}` }, svg);
}

function keyed(store, layer, id, make) {
  let el = store[id];
  if (!el) { el = make(); store[id] = el; layer.appendChild(el); }
  return el;
}

function prune(store, keep) {
  for (const id of Object.keys(store)) if (!keep.has(id)) { store[id].remove(); delete store[id]; }
}

// v0.9.8.6: an open fold draws at most FOLD_DRAW_MAX of its finished subagents, the newest first
// (a coordinator can have hundreds); the fold names how many are drawn. The list shows all.
const FOLD_DRAW_MAX = 40;

function foldDrawn(parentId) {
  const parent = (app.view?.agents || []).find((x) => x.identity === parentId);
  const ids = parent?.finished_subagents || [];
  const key = `${parentId}:${ids.length}:${app.view?.version}`;
  graph.foldCache = graph.foldCache || {};
  if (graph.foldCache[parentId]?.key !== key) {
    const byId = new Map((app.view?.agents || []).map((x) => [x.identity, x]));
    const kids = ids.map((id) => byId.get(id)).filter((x) => x && x.present)
      .sort((x, y) => (y.last_activity_at || 0) - (x.last_activity_at || 0) || x.identity.localeCompare(y.identity));
    graph.foldCache[parentId] = { key, total: kids.length, drawn: new Set(kids.slice(0, FOLD_DRAW_MAX).map((x) => x.identity)) };
  }
  return graph.foldCache[parentId];
}

function graphSubagentVisible(a) {
  if (!a.in_process) return true;
  if (graph.hideSubagents) return false;
  if (!a.folded) return true;
  return graph.expandedSubagents.has(a.fold_parent) && foldDrawn(a.fold_parent).drawn.has(a.identity);
}

function visibleAgent(a) {
  if (!graphSubagentVisible(a)) return false;
  if (!a.present) return false;
  if (graph.hiddenWorkspaces.has(a.workspace_id)) return false;
  return !graph.statusFilter.size || graph.statusFilter.has(graphState(a));
}

function drawGraph(view) {
  ensureSvg();
  updateLayout({...view,agents:view.agents.filter(graphSubagentVisible)});
  const sel = graph.selected;
  const linked = new Set();
  for (const l of view.links || []) if (l.from === sel || l.to === sel) { linked.add(l.from); linked.add(l.to); }

  const regionIds = new Set();
  for (const [id, region] of Object.entries(graph.regions)) {
    regionIds.add(id);
    const g = keyed(graph.els.regions, graph.layers.regions, id, () => s("g", { class: "region-g" },
      s("circle", { class: "region" }), s("text", { class: "region-label", "text-anchor": "middle" }),
      s("text", { class: "region-count", "text-anchor": "middle" })));
    const ws = region.ws;
    setAttrs(g, { class: `region-g${graph.hiddenWorkspaces.has(id) ? " filtered" : ""}`, "data-ws": id });
    setAttrs(g.children[1], { text: ws.label });
    setAttrs(g.children[2], { text: ws.attention ? `${ws.attention} to look at` : "" });
  }
  prune(graph.els.regions, regionIds);
  placeRegionLabels();

  const nodeIds = new Set();
  for (const [id, n] of Object.entries(graph.nodes)) {
    nodeIds.add(id);
    const a = n.agent, p = worldPos(id);
    const g = keyed(graph.els.nodes, graph.layers.nodes, id, () => {
      const el = s("g", { tabindex: "0", role: "button", "data-id": id },
        s("circle", { class: "hit" }), s("circle", { class: "body" }), s("circle", { class: "alert-ring" }),
        s("text", { class: "glyph", "text-anchor": "middle", "aria-hidden": "true" }),
        s("text", { class: "node-label", "text-anchor": "middle" }), s("text", { class: "role-label", "text-anchor": "middle" }),
        s("use", { class: "i watch-eye", href: "#i-eye" }),
        s("g", { class: "svc", "aria-hidden": "true" }, s("rect", {}), s("text", { "text-anchor": "middle" })),
        s("title", {}),
        s("foreignObject", { class: "acct-fo", x: -9, y: -9, width: 22, height: 22, "aria-hidden": "true" }),
        s("g", { class: "motion", "aria-hidden": "true" }, s("circle", { class: "m-ring" }), s("circle", { class: "m-tick", r: "2.2" })));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectNode(id); } });
      return el;
    });
    const state = graphState(a);
    const nodata = metricValue(a, graph.size) === null;
    const classes = ["node", `s-${state}`, motionOf(a, state), a.kind === "failure" ? "failure" : "", a.kind === "stall" ? "stall" : "",
      a.state === "parked" ? "parked" : "", nodata ? "nodata" : "", id === sel ? "selected" : "",
      sel && id !== sel && !linked.has(id) ? "dim" : "", visibleAgent(a) ? "" : "filtered"].filter(Boolean).join(" ");
    const runs = runtimeSays(a);
    setAttrs(g, { class: classes, transform: `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`,
      "aria-label": `${displayName(a)}: ${STATE_SAYS[state]}, ${a.label}${a.waiting_for ? `, ${waitingSays(a)}` : ""}, ${a.workspace_label}${runs ? `, ${runs}` : ""}` });
    const service = a.runtime && a.runtime.service;
    // One mark per node: the account icon when the account is recorded, else the 0.9.5 service pill.
    const fo = g.children[9], icon = compactAccountIcon(a), iconKey = icon ? JSON.stringify(icon) : "";
    if (fo.dataset.key !== iconKey) {
      fo.dataset.key = iconKey;
      const mark = icon ? accountIcon(icon, { size: 14, text: false }) : null;
      fo.replaceChildren(...(mark ? [mark] : []));
    }
    const account = !!fo.firstChild;
    setAttrs(fo, { visibility: account ? "visible" : "hidden", "data-account": account ? icon.account : null });
    setAttrs(g.children[7], { visibility: service && !account ? "visible" : "hidden", "data-service": service ? service.id : null });
    // v0.9.8.6: the pill names the service ("Claude", "Codex") and only stands in for the account mark;
    // the model and effort are always on the name's second line, the same for every service.
    setAttrs(g.children[7].children[1], { text: service ? shortenLabel(service.name || service.label, 14) : "" });
    setAttrs(g.children[8], { text: nodeTooltip(a, state) });
    const [hit, body, ring, glyph, label, role] = g.children;
    setAttrs(hit, { r: (n.r + 8).toFixed(1) });
    setAttrs(body, { r: n.r.toFixed(1) });
    setAttrs(ring, { r: (n.r + 4).toFixed(1), visibility: agentTier(a) === "waiting_on_you" ? "visible" : "hidden" });
    const [motionRing, tick] = g.children[10].children;
    setAttrs(motionRing, { r: (n.r + 5).toFixed(1) });
    setAttrs(tick, { cy: (-n.r - 5).toFixed(1) });
    setAttrs(g.children[6], { visibility: watchCloselyList().includes(id) ? "visible" : "hidden" });
    setAttrs(glyph, { y: Math.min(5, n.r * 0.35).toFixed(1), text: statusSymbol(a), "font-size": Math.max(10, Math.min(18, n.r * 0.9)).toFixed(0) });
    setAttrs(label, { y: (n.r + 4).toFixed(1) });
    setNodeLabel(label, a, window.innerWidth < 900 ? 17 : 26, false);   // text before the first fit
    setAttrs(role, { y: (-n.r - 7).toFixed(1), text: nodeRoleText(a) });
  }
  prune(graph.els.nodes, nodeIds);

  const hiddenIds = new Set();
  for (const [id, p] of Object.entries(graph.hiddenEnds)) {
    hiddenIds.add(id);
    const g = keyed(graph.els.hidden, graph.layers.hidden, id, () => s("g", { class: "hidden-node" },
      s("circle", { r: 11 }), s("text", { "text-anchor": "middle", y: 4, text: "⋯" }), s("title", {})));
    setAttrs(g, { transform: `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})` });
    setAttrs(g.children[2], { text: p.label || "hidden or unknown agent" });
  }
  prune(graph.els.hidden, hiddenIds);

  const edgeIds = new Set(), labelIds = new Set(), flowIds = new Set();
  for (const l of view.links || []) {
    const from = l.from_hidden ? graph.hiddenEnds[l.from] : worldPos(l.from);
    const to = l.hidden ? graph.hiddenEnds[l.to] : worldPos(l.to);
    if (!from || !to) continue;
    const id = `${l.from}|${l.to}|${l.kind}`;
    edgeIds.add(id);
    const path = keyed(graph.els.edges, graph.layers.edges, id, () => s("path", { "marker-end": `url(#${graph.markerId || "arrow"})` }, s("title", {})));
    const [a, b] = l.kind === "reports_to" ? [from, to] : [to, from];
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 - Math.min(80, Math.hypot(b.x - a.x, b.y - a.y) / 4);
    if (drawFlow(l, id, { [l.from]: from, [l.to]: to }, { x: mx, y: my }, sel && !(l.from === sel || l.to === sel))) flowIds.add(id);
    const active = sel && (l.from === sel || l.to === sel);
    const inferred = !!(l.inferred || l.dashed || (l.provenance || []).includes("inferred"));
    const declared = (l.provenance || []).some((p) => p === "manual" || p === "registered");
    const fromAgent = graph.nodes[l.from], toAgent = graph.nodes[l.to];
    const cross = fromAgent && toAgent && fromAgent.ws !== toAgent.ws;
    setAttrs(path, { class: ["edge", "rel", l.kind, inferred ? "inferred" : declared ? "declared" : "observed", l.cycle ? "cycle" : "",
      active ? "active" : sel ? "dim" : "", cross ? "cross" : ""].filter(Boolean).join(" "),
      d: `M${a.x.toFixed(1)},${a.y.toFixed(1)} Q${mx.toFixed(1)},${my.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}` });
    const action = actionSays(l);
    setAttrs(path.firstChild, { text: `${KIND_WORDS[l.kind] || l.kind} · ${(l.provenance || []).join(" + ")}${l.source ? ` · ${l.source}` : ""}${inferred ? ` · confidence ${Math.round(l.confidence * 100)}%` : ""}${l.cycle ? " · part of a cycle" : ""}${action ? ` · ${action}` : ""}` });
    if (active) {
      labelIds.add(id);
      const label = keyed(graph.els.labels, graph.layers.labels, id, () => s("text", { class: "edge-label", "text-anchor": "middle" }));
      setAttrs(label, { x: (a.x + 2 * mx + b.x) / 4, y: (a.y + 2 * my + b.y) / 4 - 8 * labelScale(),
        "data-x": (a.x + 2 * mx + b.x) / 4, "data-y": (a.y + 2 * my + b.y) / 4,
        text: `${KIND_WORDS[l.kind] || l.kind}${inferred ? ` · inferred ${Math.round(l.confidence * 100)}%` : ""}${l.last_action ? ` · ${ACTION_SAYS[l.last_action.kind]} ${duration(actionAge(l.last_action))} ago` : ""}` });
    }
  }
  prune(graph.els.edges, edgeIds);
  prune(graph.els.flows, flowIds);
  prune(graph.els.labels, labelIds);
  applyTransform();
}

// ---------------------------------------------------------------- viewport

function svgRect() { return graph.svg ? graph.svg.getBoundingClientRect() : { left: 0, top: 0, width: 360, height: 640 }; }

// Bars drawn over the canvas (top bar, status header, palette, tab bar, status
// chip): read from the DOM so a new header or tab bar is avoided without
// hard-coded heights. Wide overlays in the top half push the top edge down,
// those in the bottom half push the bottom edge up.
function chromeInsets() {
  const r = svgRect();
  const out = { top: 8, bottom: r.height };
  const sheet = graph.shell && graph.shell.sheet;
  const seen = new Set([...(graph.shell ? graph.shell.root.children : []), ...document.body.children]);
  for (const el of seen) {
    if (el === graph.svg || el === sheet || el.contains(graph.svg) || el.hidden) continue;
    const pos = getComputedStyle(el).position;
    if (pos !== "fixed" && pos !== "absolute" && pos !== "sticky") continue;
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height || b.height > r.height * 0.4 || b.width < Math.min(r.width * 0.4, 300)) continue;
    if (b.right < r.left || b.left > r.right) continue;
    const mid = (b.top + b.bottom) / 2 - r.top;
    if (mid < r.height / 2) out.top = Math.max(out.top, b.bottom - r.top + 4);
    else out.bottom = Math.min(out.bottom, b.top - r.top - 4);
  }
  return out;
}

// The part of the canvas not covered by bars, the sheet or the side panel.
function freeRect() {
  const r = svgRect();
  const inset = chromeInsets();
  const box = { left: 0, top: inset.top, right: r.width, bottom: inset.bottom };
  const sheet = graph.shell && graph.shell.sheet;
  if (sheet && !sheet.hidden) {
    const sr = sheet.getBoundingClientRect();
    if (window.innerWidth >= 900) box.right = Math.min(box.right, sr.left - r.left - 8);
    else box.bottom = Math.min(box.bottom, sr.top - r.top - 8);
  }
  return box;
}

// Applied immediately: one attribute write per pointer event is cheap and the
// browser paints once per frame anyway, so the drawing never lags the state.
function applyTransform() {
  if (!graph.svg || !graph.transform) return;
  const t = graph.transform;
  graph.viewport.setAttribute("transform", `translate(${t.x.toFixed(2)} ${t.y.toFixed(2)}) scale(${t.k.toFixed(4)})`);
  applyLabelScale();
}

// Labels stay at least 12 px on screen: when zoomed out they grow in world
// units (capped), and role labels are hidden below a zoom threshold.
const LABEL_SCALE_MAX = 5;
const ROLE_LABEL_MIN_ZOOM = 0.75;

function labelScale() {
  const k = graph.transform ? graph.transform.k : 1;
  return Math.min(LABEL_SCALE_MAX, Math.max(1, 1 / k));
}

function applyLabelScale() {
  const scale = Math.round(labelScale() * 100) / 100;
  graph.svg.classList.toggle("zoomed-out", graph.transform.k < ROLE_LABEL_MIN_ZOOM);
  graph.labelScale = scale;
  graph.svg.style.setProperty("--label-scale", String(scale));
  placeRegionLabels();
  placeNodeLabels();
}

function shortenLabel(text, limit) {
  return text.length > limit ? text.slice(0, Math.max(1, limit - 1)) + "…" : text;
}

// Titles own the top of the bubble. The padding in updateLayout keeps dots out
// of this band; the full workspace name is available on tap and in the tooltip.
function placeRegionLabels() {
  const scale = labelScale();
  for (const [id, region] of Object.entries(graph.regions)) {
    const g = graph.els.regions[id];
    if (!g) continue;
    region.band = 0;
    setAttrs(g.children[0], { cx: region.cx, cy: region.cy, r: region.R });
    const limit = Math.max(4, Math.floor(region.R * 1.45 / (7.6 * scale)));
    const words = region.ws.label.toUpperCase().split(/[-\s]+/);
    const lines = [""];
    for (const word of words) {
      const i = lines.length - 1;
      if (lines[i] && (lines[i] + " " + word).length > limit && lines.length < 2) lines.push(word);
      else lines[i] += (lines[i] ? " " : "") + word;
    }
    // Keep the last word when the second line would be cut: "herdr-dash-notif-read"
    // and "herdr-dashboard" must not both read "HERDR DASH…".
    const last = words[words.length - 1];
    if (lines.length === 2 && lines[1].length > limit && lines[1] !== last) lines[1] = "…" + last;
    const title = g.children[1];
    title.replaceChildren(...lines.map((line, i) => s("tspan", { x: region.cx, dy: i ? 14 * scale : 0, text: shortenLabel(line, limit) })));
    setAttrs(title, { x: region.cx, y: region.cy - region.R * 0.92, "aria-label": region.ws.label });
    setAttrs(g.children[2], { text: "" });
    let tip = g.querySelector("title");
    if (!tip) { tip = s("title", {}); g.appendChild(tip); }
    setAttrs(tip, { text: region.ws.label });
  }
}

function overlapsRect(a, b, gap = 3) {
  return a.left < b.right + gap && a.right + gap > b.left && a.top < b.bottom + gap && a.bottom + gap > b.top;
}

// Resolve labels in screen coordinates after every zoom or data patch. Dots
// and workspace titles are obstacles. Lower-priority labels yield if no slot
// fits; the complete name remains accessible on the node and in its inspector.
function placeNodeLabels() {
  const k = graph.transform?.k || 1, scale = labelScale();
  const ordered = Object.entries(graph.nodes).sort(([id, n], [other, m]) =>
    Number(agentTier(m.agent) === "waiting_on_you") - Number(agentTier(n.agent) === "waiting_on_you")
    || Number(other === graph.selected) - Number(id === graph.selected)
    || Number(!!agentTier(m.agent)) - Number(!!agentTier(n.agent))   // any "to look at" item keeps its name
    || id.localeCompare(other));
  const occupied = [], dots = {};
  for (const [id, n] of ordered) {
    const g = graph.els.nodes[id];
    const r = Math.max(n.r, 7.5 / k);
    setAttrs(g.children[1], { r });
    setAttrs(g.children[2], { r: r + 4 / k });
    setAttrs(g.children[3], { "font-size": 12 / k, y: 4 / k });
    const eye = g.children[6];
    setAttrs(eye, { x: r + 2 / k, y: -r - 10 / k, width: 14 / k, height: 14 / k });
    // Service badge: a small pill on the lower right of the ring, the same size on screen at every zoom.
    if (visibleAgent(n.agent)) {
      occupied.push(dots[id] = g.children[1].getBoundingClientRect());
      if (eye.getAttribute("visibility") === "visible") occupied.push(eye.getBoundingClientRect());
    }
  }
  for (const group of Object.values(graph.els.regions)) {
    const title = group.children[1], y = Number(title.getAttribute("y"));
    // At a very small zoom the fixed-size title can outgrow its band. Move
    // it above the bubble until it clears dots and other titles.
    for (let step = 0; step < 100; step++) {
      setAttrs(title, { y: y - step * 16 / k });
      if (!occupied.some(b => overlapsRect(title.getBoundingClientRect(), b))) break;
    }
    occupied.push(title.getBoundingClientRect());
  }
  for (const el of Object.values(graph.els.labels)) {
    const x = Number(el.getAttribute("data-x")), y = Number(el.getAttribute("data-y"));
    for (let step = 0; step < 100; step++) {
      const offset = step ? Math.ceil(step / 2) * (step % 2 ? -1 : 1) * 18 / k : 0;
      setAttrs(el, { x, y: y - 8 / k + offset });
      if (!occupied.some(b => overlapsRect(el.getBoundingClientRect(), b))) break;
    }
    occupied.push(el.getBoundingClientRect());
  }
  const canvas = graph.svg.getBoundingClientRect();
  const limit = window.innerWidth < 900 ? 17 : 26;
  const free = (rect, own) => rect.left >= canvas.left && rect.right <= canvas.right
    && !occupied.some((b) => b !== own && overlapsRect(rect, b));
  const fits = (el, candidates) => {
    for (const [slot, [x, y, anchor]] of candidates.entries()) {
      placeText(el, x, y, anchor);
      const rect = el.getBoundingClientRect();
      if (!free(rect)) continue;
      occupied.push(rect);
      return { slot, x, y, anchor, rect };
    }
    return null;
  };
  // Names and roles first, in priority order, exactly as without the model line.
  const named = [];
  for (const [id, n] of ordered) {
    const g = graph.els.nodes[id], label = g.children[4], role = g.children[5];
    const r = Math.max(n.r, 7.5 / k);
    setAttrs(role, { text: shortenLabel(nodeRoleText(n.agent), limit) });
    setNodeLabel(label, n.agent, limit, false);
    label.style.display = role.style.display = "";
    const shown = visibleAgent(n.agent);
    const spot = shown && fits(label, [[0, r + 7 * scale, "middle"], [0, -r - 19 * scale, "middle"],
      [r + 8 * scale, -5 * scale, "start"], [-r - 8 * scale, -5 * scale, "end"]]);
    if (spot) named.push([n, label, spot]); else label.style.display = "none";
    // Role: above the dot, else with its name (under it, then above it), on the side the name went.
    const roleSlots = [[0, -r - 9 * scale, "middle"]];
    if (spot) roleSlots.push([spot.x, spot.y + 27 * scale, spot.anchor], [spot.x, spot.y - 4 * scale, spot.anchor]);
    else roleSlots.push([0, r + 34 * scale, "middle"]);
    if (!role.textContent || !shown || !fits(role, roleSlots)) role.style.display = "none";
  }
  // Then the node's one mark (account icon, else service pill) on the first free corner of the ring
  // (upper right first: names sit below). Names and roles win: a mark with no free corner waits until
  // there is room (zoom in); the tooltip and the inspector always have it.
  for (const [id, n] of ordered) {
    const g = graph.els.nodes[id], badge = g.children[7], text = badge.children[1], fo = g.children[9];
    badge.style.display = fo.style.display = "none";
    const account = fo.getAttribute("visibility") === "visible";
    if ((!account && badge.getAttribute("visibility") !== "visible") || !visibleAgent(n.agent)) continue;
    const r = Math.max(n.r, 7.5 / k), h = 12 / k, w = (text.textContent.length * 5.5 + 7) / k;
    const dx = r * 0.71 + (account ? 4 : 2) / k, dy = r * 0.71 + (account ? 3 : 1) / k;
    const mark = account ? fo : badge;
    mark.style.display = "";
    for (const [sx, sy] of [[1, -1], [-1, -1], [1, 1], [-1, 1]]) {
      if (account) setAttrs(fo, { transform: `translate(${(sx * dx).toFixed(2)} ${(sy * dy).toFixed(2)}) scale(${(1 / k).toFixed(4)})` });
      else {
        setAttrs(badge.children[0], { x: sx * dx - w / 2, y: sy * dy - h / 2, width: w, height: h, rx: h / 2 });
        setAttrs(text, { x: sx * dx, y: sy * dy + 3 / k, "font-size": 8.5 / k });
      }
      const rect = (account ? fo : badge.children[0]).getBoundingClientRect();
      if (!occupied.some((b) => b !== dots[id] && overlapsRect(rect, b, 1))) { occupied.push(rect); break; }
      if (sx === -1 && sy === 1) mark.style.display = "none";
    }
  }
  // Then model and effort as a second line, only where it fits: it never moves or hides another label.
  for (const [n, label, spot] of named) {
    if (!runtimeLine(n.agent, limit)) continue;
    setNodeLabel(label, n.agent, limit, true);
    placeText(label, spot.x, spot.y - [0, 14.3, 7.15, 7.15][spot.slot] * scale, spot.anchor);
    const rect = label.getBoundingClientRect();
    if (free(rect, spot.rect)) occupied[occupied.indexOf(spot.rect)] = rect;
    else { setNodeLabel(label, n.agent, limit, false); placeText(label, spot.x, spot.y, spot.anchor); }
  }
  // Reappend only when necessary, so nodes waiting on Alex always paint last.
  for (const [id, n] of ordered.slice().reverse()) if (agentTier(n.agent) === "waiting_on_you" || id === graph.selected)
    graph.layers.nodes.appendChild(graph.els.nodes[id]);
}

function setTransform(t, userAction = true) {
  graph.transform = { k: Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, t.k)), x: t.x, y: t.y };
  if (userAction) graph.userMoved = true;
  applyTransform();
  saveGraphState();
}

function fitView(avoidSheet = true) {
  const r = svgRect();
  const inset = chromeInsets();
  const b = bounds(), f = avoidSheet ? freeRect() : { left: 0, top: inset.top, right: r.width, bottom: inset.bottom };
  const w = Math.max(100, f.right - f.left - 24), hgt = Math.max(100, f.bottom - f.top - 24);
  const k = Math.max(ZOOM_MIN, Math.min(w / b.w, hgt / b.h, 1.6));
  setTransform({ k, x: f.left + 12 + (w - b.w * k) / 2 - b.x * k, y: f.top + 12 + (hgt - b.h * k) / 2 - b.y * k }, false);
}

// The automatic first fit: around the side panel on wide screens; on phones
// the whole canvas (a bottom sheet would leave too little), then the selected
// node is moved out from under the sheet.
function autoFit() {
  fitView(window.innerWidth >= 900);
  if (graph.selected) reveal(graph.selected);
}

// v0.9.8.6: after subagents are shown or hidden, fit the camera to the new size, unless Alex has
// moved or zoomed the graph himself (then his view stays).
function refitAfterChange() {
  if (graph.userMoved) return;
  graph.autoFit = true;
  setTimeout(autoFit, 0);
}

function zoomAround(px, py, factor) {
  const t = graph.transform;
  const k = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, t.k * factor));
  setTransform({ k, x: px - (px - t.x) * (k / t.k), y: py - (py - t.y) * (k / t.k) });
}

function zoomButton(factor) {
  const f = freeRect();
  zoomAround((f.left + f.right) / 2, (f.top + f.bottom) / 2, factor);
}

// Keep a node out from under the sheet: pan just enough (never zoom).
function reveal(identity) {
  const p = worldPos(identity);
  if (!p || !graph.transform) return;
  const t = graph.transform, f = freeRect(), m = Math.max(8, Math.min(40 + p.r * t.k, (f.bottom-f.top)/2-3));
  const sx = p.x * t.k + t.x, sy = p.y * t.k + t.y;
  let dx = 0, dy = 0;
  if (sx < f.left + m) dx = f.left + m - sx; else if (sx > f.right - m) dx = f.right - m - sx;
  if (sy < f.top + m) dy = f.top + m - sy; else if (sy > f.bottom - m) dy = f.bottom - m - sy;
  if (dx || dy) setTransform({ ...t, x: t.x + dx, y: t.y + dy }, false);
}

function showRegion(wsId) {
  const region = graph.regions[wsId];
  if (!region) return;
  const f = freeRect(), w = f.right - f.left, hgt = f.bottom - f.top;
  const k = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.min(w, hgt) / (2 * region.R + 80)));
  setTransform({ k, x: f.left + w / 2 - region.cx * k, y: f.top + hgt / 2 - region.cy * k });
}

// ---------------------------------------------------------------- gestures

function attachGestures(svg) {
  const local = (e) => { const r = svg.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const baseline = () => {
    // Called whenever the number of fingers changes: start from the CURRENT
    // transform, so lifting a finger never reverts the zoom.
    const pts = [...graph.pointers.values()];
    const t = { ...graph.transform };
    if (pts.length >= 2) {
      const [a, b] = pts;
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      graph.gesture = { type: "pinch", dist: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), k: t.k,
                        world: { x: (mid.x - t.x) / t.k, y: (mid.y - t.y) / t.k } };
    } else if (pts.length === 1) {
      graph.gesture = { type: "pan", start: { ...pts[0] }, t };
    } else {
      graph.gesture = null;
    }
  };
  svg.addEventListener("pointerdown", (e) => {
    if (e.button !== undefined && e.button > 0) return;
    try { svg.setPointerCapture(e.pointerId); } catch (_) { /* pointer not capturable: still tracked */ }
    graph.pointers.set(e.pointerId, local(e));
    graph.interacting = true;
    const node = e.target.closest && e.target.closest("[data-id]");
    graph.tap = graph.pointers.size === 1
      ? { id: node ? node.getAttribute("data-id") : null, at: local(e), type: e.pointerType || "mouse", time: e.timeStamp,
          hidden: !!(e.target.closest && e.target.closest(".hidden-node")) }
      : null;                                   // a second finger is never a tap
    baseline();
  });
  svg.addEventListener("pointermove", (e) => {
    if (!graph.pointers.has(e.pointerId)) return;
    graph.pointers.set(e.pointerId, local(e));
    const g = graph.gesture;
    if (!g) return;
    if (g.type === "pinch") {
      const [a, b] = [...graph.pointers.values()];
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const k = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, g.k * Math.hypot(a.x - b.x, a.y - b.y) / g.dist));
      // The world point that was under the fingers stays under their midpoint.
      setTransform({ k, x: mid.x - g.world.x * k, y: mid.y - g.world.y * k });
    } else {
      const p = graph.pointers.get(e.pointerId);
      const dx = p.x - g.start.x, dy = p.y - g.start.y;
      if (graph.tap && Math.hypot(dx, dy) > (TAP_SLOP[graph.tap.type] || 6)) graph.tap = null;
      if (!graph.tap) setTransform({ ...g.t, x: g.t.x + dx, y: g.t.y + dy });
    }
  });
  const end = (e, cancelled) => {
    if (!graph.pointers.has(e.pointerId)) return;
    graph.pointers.delete(e.pointerId);
    try { if (svg.hasPointerCapture(e.pointerId)) svg.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    if (!cancelled && graph.pointers.size === 0 && graph.tap && e.timeStamp - graph.tap.time < 600 && !graph.tap.hidden) {
      selectNode(graph.tap.id);
    }
    if (graph.pointers.size > 0) graph.tap = null;
    baseline();
    if (graph.pointers.size === 0) { graph.interacting = false; graph.tap = null; }
  };
  svg.addEventListener("pointerup", (e) => end(e, false));
  svg.addEventListener("pointercancel", (e) => end(e, true));
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = local(e);
    // Trackpad pinch arrives as ctrl+wheel; both zoom around the cursor.
    const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
    zoomAround(p.x, p.y, factor);
  }, { passive: false });
}

// Keyboard: Escape closes the sheet, + / - zoom, 0 fits (not while typing).
document.addEventListener("keydown", (e) => {
  if (route().name !== "graph" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement && document.activeElement.tagName)) return;
  if (e.key === "Escape" && graph.shell && !graph.shell.sheet.hidden) closeSheet();
  else if (e.key === "+" || e.key === "=") zoomButton(1.35);
  else if (e.key === "-") zoomButton(1 / 1.35);
  else if (e.key === "0") fitView(true);
});

// ---------------------------------------------------------------- selection

function selectNode(identity) {
  const target = identity ? hashLink('graph', identity) : hashLink('home');
  if (location.hash !== target) location.hash = target;
}

// ---------------------------------------------------------------- shell

// Icons come from the inline sprite in index.html (one set, see docs/THEME-v0.8.md).
const ICON_IDS = { attention: "warn", workspaces: "graph", view: "eye", list: "list", settings: "gear", bell: "bell", fit: "fit" };

function icon(name, cls = "tool-icon") {
  return s("svg", { class: `i ${cls}`, "aria-hidden": "true" }, s("use", { href: `#i-${ICON_IDS[name] || name}` }));
}

const PANEL_SWITCH = [["view", "View"], ["workspaces", "Workspaces"], ["attention", "To look at"]];

function panelSwitch() {
  return h("div", { class: "segmented panel-switch", role: "group", "aria-label": "Graph tools" },
    PANEL_SWITCH.map(([panel, label]) => h("button", { type: "button", "aria-pressed": String(graph.panel === panel),
      on: { click: () => { graph.panel = panel; graphRefresh(true); } }, text: label })));
}

function buildGraphShell() {
  const svg = ensureSvg();
  const tool = (name, label, onClick) => h("button", { type: "button", class: "tool", "data-tool": name, "aria-label": label,
    on: { click: onClick } }, icon(name), h("span", { class: "tool-label", text: label }), h("span", { class: "tool-badge", hidden: true }));
  const shell = {
    root: h("div", { class: "graph-shell" }),
    status: h("div", { class: "graph-status", role: "status" }),
    note: h("div", { class: "graph-note", hidden: true }),
    sheet: h("aside", { class: "sheet", hidden: true, "aria-live": "polite" }),
  };
  shell.sheetHead = h("div", { class: "sheet-head" },
    h("button", { type: "button", class: "sheet-grip", "aria-label": "Expand or shrink", on: { click: () => {
      graph.sheetExpanded = !graph.sheetExpanded; shell.sheet.classList.toggle("expanded", graph.sheetExpanded); } } }),
    h("span", { class: "sheet-title" }),
    h("button", { type: "button", class: "btn quiet sheet-close", "aria-label": "Close", on: { click: closeSheet }, text: "Close" }));
  shell.sheetBody = h("div", { class: "sheet-body" });
  shell.sheet.append(shell.sheetHead, shell.sheetBody);
  const togglePanel = (name) => () => { graph.panel = graph.panel === name ? null : name; if (graph.panel && graph.selected) selectNode(null); graphRefresh(true); };
  // One "View" control; its sheet switches between view, workspaces and "to look at".
  // Navigation (Graph · List · Notifications · Settings) is the tab bar in index.html.
  shell.palette = h("nav", { class: "palette", "aria-label": "Graph tools" },
    tool("view", "View", () => { graph.panel = PANEL_SWITCH.some(([p]) => p === graph.panel) ? null : "view";
      if (graph.panel && graph.selected) selectNode(null); graphRefresh(true); }));
  shell.zoom = h("div", { class: "zoom-tools" },   // pinch zooms; one button fits everything
    h("button", { type: "button", class: "icon-btn", "aria-label": "Fit everything", title: "Fit", on: { click: () => fitView(true) } }, icon("fit")));
  shell.questions = h("details",{class:"graph-questions",hidden:true,"data-reading-key":"graph-questions"});
  shell.questionsTitle=h("summary",{text:"Questions for you"});shell.questionsBody=h("div",{});
  shell.questions.append(shell.questionsTitle,shell.questionsBody);
  shell.questions.addEventListener("toggle",()=>{if(!shell.questions.open && (app.view?.questions || []).some(q=>q.keep_open))shell.questions.open=true;syncChromeBottom();if(graph.autoFit&&!graph.userMoved)autoFit();else if(graph.selected)reveal(graph.selected);});
  // Top: only what needs Alex. Folds and empty workspaces live in the View and
  // Workspaces sheets. The toolbar is its own layer: bottom (thumb reach) under 900 px.
  shell.top = h("div", { class: "graph-top" }, shell.status, shell.questions);
  // The wide-screen toolbar sits under the top block: follow every size change
  // (Questions opened, header grew), not only polls, so it never covers them.
  new ResizeObserver(syncChromeBottom).observe(shell.top);
  shell.root.append(svg, shell.top,
    h("div", { class: "graph-toolbar" }, shell.palette, visualsEntry(), h("span",{class:"quota-slot","data-quota-chip":""},h("a",{class:"chip quota-chip",href:hashLink("quotas"),text:"Quotas"})), shell.zoom), shell.note, shell.sheet);
  graph.shell = shell;
  return shell.root;
}

function syncChromeBottom() {
  const shell = graph.shell;
  if (shell?.top) shell.root.style.setProperty("--graph-chrome-bottom", `${shell.top.getBoundingClientRect().bottom}px`);
}

function closeSheet() {
  if (graph.selected) selectNode(null);
  graph.panel = null;
  graphRefresh(true);
}

// Called for every refresh and route change while the graph is shown.
function graphRefresh(force = false) {
  const view = app.view;
  if (!view || !graph.shell) return;
  const r = route();
  graph.selected = r.name === "graph" ? r.id || null : null;
  if (graph.selected) graph.panel = null;
  drawGraph(view);
  if (!graph.fitted) {                      // once per page load, never on refresh
    graph.fitted = true;
    graph.transform = graph.transform || { k: 0.6, x: 0, y: 0 };
    if (!graph.restored) {
      graph.autoFit = true;
      setTimeout(autoFit, 0);
    }
  }
  updateChrome(view);
  updateSheet(view, force);
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function updateChrome(view) {
  const shell = graph.shell;
  for (const b of shell.palette.querySelectorAll("button.tool"))
    b.setAttribute("aria-pressed", String(PANEL_SWITCH.some(([p]) => p === graph.panel)));
  // The shared status header (app.js). Swap it only when its content changes,
  // so a poll never replaces the element under a finger (a lost tap).
  const header = statusHeader(view, { fresh: true });
  header.classList.add("graph-status");
  if (header.outerHTML !== shell.statusKey) {
    shell.statusKey = header.outerHTML;
    shell.status.replaceWith(header);
    shell.status = header;
  }
  renderFresh();
  const entries=questionEntries(view);
  if (!shell.questions.contains(document.activeElement)) shell.questions.hidden=!entries.length;
  if ((view.questions || []).some(q=>q.keep_open)) shell.questions.open=true;
  shell.questionsTitle.textContent=`Questions for you · ${openQuestionCount(entries)}`;
  updateReadingView(shell.questionsBody,questionsBlock(view));
  syncChromeBottom();
  let note = null;
  if (!view.workspaces.length) note = h("div", {}, h("p", { text: "No workspaces are watched yet." }),
    h("button", { class: "btn primary", type: "button", on: { click: () => { graph.panel = "settings"; graphRefresh(true); } }, text: "Choose workspaces" }));
  else if (!view.agents.some((a) => a.present)) note = h("p", { text: "No live agents in the watched workspaces." });
  else if (!view.agents.some(visibleAgent)) note = h("div", {}, h("p", { text: "Every agent is filtered out." }),
    h("button", { class: "btn", type: "button", on: { click: () => { graph.hiddenWorkspaces.clear(); graph.statusFilter.clear(); graph.hideSubagents=false; graphRefresh(true); } }, text: "Show all" }));
  shell.note.hidden = !note;
  shell.note.replaceChildren(...(note ? [note] : []));
}

// What the inspector shows, minus timestamps that tick on every refresh.
function stableKey(a) {
  const m = a.metrics || {};
  return JSON.stringify({ ...a, last_seen: 0, metrics: { ...m, trace: { ...(m.trace || {}), read_at: 0 } } });
}

function updateSheet(view, force) {
  const shell = graph.shell;
  const agent = graph.selected ? view.agents.find((a) => a.identity === graph.selected) : null;
  if (app.sheetScreen && app.sheetScreen.identity !== agent?.identity) app.sheetScreen = null;
  if (!agent && !graph.panel) { shell.sheet.hidden = true; graph.inspectorKey = ""; graph.panelKey = ""; return; }
  const wasHidden = shell.sheet.hidden;
  shell.sheet.hidden = false;
  // Rebuild only when what it shows changed, and never while you type in it.
  const busy = shell.sheet.contains(document.activeElement) && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);
  let key, title, build;
  if (agent) {
    const detail = app.details[agent.identity];
    key = `a:${agent.identity}:${app.sheetScreen?.screen || ""}:${stableKey(agent)}:${detail ? detail.generated_at : ""}:${JSON.stringify(graph.conversation[agent.identity] || "")}:${JSON.stringify(app.outputs[agent.identity] || "")}:${app.ackErrors[agent.identity] || ""}`;
    title = [agent.workspace_label, agent.tab_label].filter(Boolean).join(" › ");
    build = () => inspector(agent);
  } else {
    key = `p:${graph.panel}:${view.version}:${app.chooser ? 1 : 0}:${app.audit ? 1 : 0}:${graph.size}:${[...graph.hiddenWorkspaces]}:${[...graph.statusFilter]}:${graph.hideSubagents}:${[...graph.expandedSubagents]}`;
    title = { attention: "To look at", workspaces: "Workspaces", view: "View and legend", settings: "Settings" }[graph.panel];
    const panel = { attention: attentionPanel, workspaces: workspacesPanel, view: viewPanel, settings: settingsPanel }[graph.panel];
    build = PANEL_SWITCH.some(([p]) => p === graph.panel) ? (v) => h("div", { "data-reading-key": `panel:${graph.panel}` }, panelSwitch(), panel(v)) : panel;
  }
  if (!force && ((!agent && busy) || key === (agent ? graph.inspectorKey : graph.panelKey))) return;
  if (agent) graph.inspectorKey = key; else graph.panelKey = key;
  const scroll = shell.sheetBody.scrollTop;
  const sameAgent = agent && shell.sheetBody.firstChild?.getAttribute("data-reading-key") === `inspector:${agent.identity}`;
  shell.sheet.querySelector(".sheet-title").textContent = title;
  if (sameAgent) updateReadingView(shell.sheetBody, build(view));
  else if (!wasHidden && !agent) updateReadingView(shell.sheetBody, build(view));
  else shell.sheetBody.replaceChildren(build(view));
  if (!wasHidden) shell.sheetBody.scrollTop = scroll;
  decorateSavedControls(shell.sheetBody);
  if (agent && wasHidden) setTimeout(() => reveal(agent.identity), 0);
}

// ---------------------------------------------------------------- panels

function attentionPanel(view) {
  const items = attentionItems(view).filter(a=>!a.quota_stop);
  if (!items.length && !(view.quota_stops?.groups || []).length) return h("p", { class: "muted", text: "Nothing needs a look right now." });
  // Grouped like the list: same tiers, same headings, same order. v0.9.8.6: one compact row per agent
  // (state, name with its account mark and model chip, the reason on two lines at most, workspace and
  // age); the read hint once per section. The row was a flex line that the hint squeezed to a sliver.
  return h("div", {}, quotaStopBlock(view), tierOrder().map((tier) => [tier, items.filter((a) => agentTier(a) === tier)
    .sort((x, y) => (y.event_at || 0) - (x.event_at || 0))]).filter(([, list]) => list.length).map(([tier, list]) => {
    const hint = groupReadHint(list);
    return h("section", { class: "attention-group" },
      h("h2", { class: "attention-tier", text: `${tierHead(tier)} · ${list.length}` }),
      hint ? h("p", { class: "muted small read-state-hint section-hint", "data-reading-key": `hint:${tier}`, text: hint }) : null,
      h("ul", { class: "panel-list att-panel-list" }, list.map((a) => h("li", {"data-reading-key":`attention:${a.identity}:${a.event_id || ""}`},
        h("button", { type: "button", class: "panel-item att-item", on: { click: () => { selectNode(a.identity); } } },
          h("span", { class: `glyph-dot s-${graphState(a)}`, "aria-hidden": "true", text: statusSymbol(a) }),
          h("span", { class: "panel-item-text" },
            h("span", { class: "att-who" }, h("b", { text: displayName(a) }), accountIcon(a.icon, { size: 14, text: false }), modelBadge(a)),
            h("span", { class: "muted small att-reason" }, summaryLine(a)),
            h("span", { class: "faint small att-where", text: [a.workspace_label, a.event_at ? ago(a.event_at) : ""].filter(Boolean).join(" · ") }))),
        attentionMarkRead(a, { sectionHint: hint })))));
  }));
}

function workspacesPanel(view) {
  return h("div", {},
    h("ul", { class: "panel-list workspace-visibility" }, activeWorkspaces(view).map((ws) => h("li", {},
      h("button", { type: "button", class: "panel-item", on: { click: () => showRegion(ws.id) } },
        h("span", { class: "panel-item-text" }, h("b", { text: ws.label }), h("span", { class: "muted small", text: `${plural(ws.agent_count, "agent")} · ${legendText(ws.counts) || "none running"}` }))),
      toggle(!graph.hiddenWorkspaces.has(ws.id), `Show ${ws.label} in graph`, (on) => {
        if (on) graph.hiddenWorkspaces.delete(ws.id); else graph.hiddenWorkspaces.add(ws.id); graphRefresh(true); })))),
    activeWorkspaces(view).filter((ws) => ws.layout_hints?.length).map((ws) => h("section", {},
      h("p", { class: "muted small", text: ws.label }), layoutHints(ws.layout_hints))),
    emptyWorkspaceGroup(view),
    h("p", { class: "note", text: "Show in graph changes this device's graph only. Tap a workspace to fly to it." }),
    h("details", { class: "watched-chooser", "data-reading-key": "watched-chooser" },
      h("summary", { text: "Watched workspaces…" }),
      h("p", { class: "note", text: "Watching controls which workspaces the dashboard monitors and sends alerts for, on every device. New workspaces start off." }),
      chooserCard()));
}

function viewPanel(view) {
  const sizeControl = h("div", { class: "segmented", role: "group", "aria-label": "Node size" },
    [["context", "Context"], ["tokens", "Tokens"], ["equal", "Equal"]].map(([value, label]) =>
      h("button", { type: "button", "aria-pressed": String(graph.size === value),
        on: { click: () => { graph.size = value; saveGraphState(); graphRefresh(true); } }, text: label })));
  const scale = SIZE_SCALE[graph.size];
  const filterChip = (state, label) => h("button", { type: "button", class: "chip", "aria-pressed": String(graph.statusFilter.has(state)),
    on: { click: () => { if (graph.statusFilter.has(state)) graph.statusFilter.delete(state); else graph.statusFilter.add(state); graphRefresh(true); } } },
    h("i", { class: `dot state s-${state}` }), label);
  const edge = (cls, label) => h("li", {}, s("svg", { width: "34", height: "12", "aria-hidden": "true" },
    s("line", { x1: "2", y1: "6", x2: "32", y2: "6", class: `edge ${cls}` })), ` ${label}`);
  return h("div", {},
    h("h3", { class: "sub", text: "Node size" }), sizeControl,
    scale ? h("p", { class: "note", text: `Size = ${scale.label}, same scale for everyone: ${scale.ref.map((v) => tokens(v)).join(" < ")}. Small dashed circles have no data.` })
      : h("p", { class: "note", text: "All nodes the same size." }),
    h("h3", { class: "sub", text: "Show only" }),
    h("div", { class: "chips" }, STATES.map((st) => filterChip(st, STATE_SHORT[st]))),
    h("div",{class:"setting"},h("span",{class:"label",text:view.subagent_labels?.hide}),
      toggle(graph.hideSubagents,view.subagent_labels?.hide || "",on=>{graph.hideSubagents=on;graphRefresh(true);refitAfterChange();})),
    graph.hideSubagents ? null : graphSubagentFolds(view),
    h("h3", { class: "sub", text: "Node colours" }),
    h("ul", { class: "legend-list" }, STATES.map((st) => h("li", {},
      h("span", { class: `glyph-dot s-${st}`, "aria-hidden": "true", text: STATE_GLYPH[st] }), ` ${STATE_SHORT[st]}`,
      st === "needs_you" ? h("span", { class: "muted small", text: " · waiting on you (approval or question), red ring" }) : null)),
      h("li", {}, h("span", { class: "glyph-dot s-caution", "aria-hidden": "true", text: "!" }), " gone while busy or failed (dashed)")),
    h("p", { class: "note", text: "Same states as the list. Herdr's own status is shown in each agent's details." }),
    h("h3", { class: "sub", text: RT.legend_title }),
    h("ul", { class: "legend-list", "data-reading-key": "legend-runtime" },
      h("li", {}, h("span", { class: "model-chip", "aria-hidden": "true", text: "Sol 6.2 \u00b7 high" }), ` ${RT.legend_badge}`),
      h("li", {}, h("span", { class: "pips", "aria-hidden": "true" }, [1, 1, 1, 0, 0].map((on) => h("i", { class: on ? "on" : "" }))),
        ` ${RT.legend_pips}`)),
    h("p", { class: "note", text: RT.legend_unknown }),
    h("h3", { class: "sub", text: "Shapes and lines" }),
    h("ul", { class: "legend-list" },
      h("li", {}, s("svg", { width: "34", height: "16", "aria-hidden": "true" }, s("circle", { cx: "17", cy: "8", r: "7", class: "region" })),
        " region = workspace (membership, not a relationship)"),
      edge("rel reports_to declared", "reports to (declared)"),
      edge("rel reports_to inferred", "reports to (inferred: dotted)"),
      edge("rel spawned_by", "spawned (trace or launch helper)"),
      edge("rel forked_from", "forked (trace)"),
      edge("rel reports_to declared cycle", "part of a reporting cycle"),
      h("li", {}, h("span", { class: "legend-q", text: "⋯" }), " hidden or unknown agent")),
    h("h3", { class: "sub", text: "Waiting and motion" }),
    h("ul", { class: "legend-list", "data-reading-key": "legend-motion" },
      h("li", {}, motionSample("m-working"), " working (breathes)"),
      h("li", {}, motionSample("m-shell"), " waiting for a command or background task (turning dashes)"),
      h("li", {}, motionSample("m-scheduled"), " sleeping or wake-up scheduled (ticks)"),
      h("li", {}, motionSample("m-worker"), " waiting for a worker (dots flow to the worker)"),
      h("li", {}, s("svg", { width: "34", height: "16", "aria-hidden": "true", class: "flow fx-task age-fresh" },
        s("line", { x1: "2", y1: "8", x2: "32", y2: "8", class: "edge rel" }), s("path", { class: "chevron", d: "M12,3.5 L21,8 L12,12.5 z" })),
        " last message on a line: coordinator sent a task"),
      h("li", {}, s("svg", { width: "34", height: "16", "aria-hidden": "true", class: "flow fx-report age-fresh" },
        s("line", { x1: "2", y1: "8", x2: "32", y2: "8", class: "edge rel" }), s("path", { class: "chevron", d: "M22,3.5 L13,8 L22,12.5 z" })),
        " worker reported back or finished its turn")),
    h("p", { class: "note", text: "The chevron points from sender to receiver; bright = under 2 min, fading out over 2 h. Waiting states come from each agent's own transcript: they are never flagged as stalled." }),
    h("p", { class: "note", text: "Declared links take precedence. Dashed reporting links use specific brief or transcript evidence; names and tabs alone never establish supervision." }));
}

function motionSample(cls) {
  return s("svg", { width: "34", height: "22", viewBox: "-17 -11 34 22", "aria-hidden": "true", class: `legend-motion ${cls}` },
    s("circle", { class: "body", r: "5" }), s("g", { class: "motion" }, s("circle", { class: "m-ring", r: "8" }), s("circle", { class: "m-tick", r: "2", cy: "-8" })));
}

// Finished subagents, folded under their parent. Open a fold to draw them.
function graphSubagentFolds(view) {
  const parents = view.agents.filter(a=>a.finished_subagents?.length);
  if (!parents.length) return null;
  return h("div",{class:"graph-subagent-folds","data-reading-key":"graph-subagent-folds"},parents.map(parent=>subagentFold(parent,{
    open:graph.expandedSubagents.has(parent.identity),
    onToggle:open=>{
      if (graph.expandedSubagents.has(parent.identity)===open) return;
      if(open)graph.expandedSubagents.add(parent.identity);else graph.expandedSubagents.delete(parent.identity);
      graphRefresh(true);
      refitAfterChange();
    }})).map((fold,i)=>{
      const parent=parents[i], d=graph.expandedSubagents.has(parent.identity)?foldDrawn(parent.identity):null;
      if (fold && d && d.total>FOLD_DRAW_MAX) fold.querySelector("summary")?.append(h("span",{class:"fold-drawn muted small","data-reading-key":"fold-drawn",text:` · ${FOLD_DRAW_MAX} newest of ${d.total} drawn`}));
      return fold;
    }));
}

function settingsPanel() {
  return settingsBody();
}

function renderGraph() {
  // Kept for the list-world agent page's "Show in graph" and old links.
  return buildGraphShell();
}
// ---------------------------------------------------------------- agent sheet parts

// Manual declarations (login + CSRF; recorded with provenance "manual").
async function relate(body) {
  try {
    await api(UI.api.relations, { method: "POST", body });
  } catch (err) {
    flash(`Not saved: ${err.message}`);
  }
  delete app.details[body.subject];
  await refresh(true);
}

function relationsEditor(agent, view) {
  const others = view.agents.filter((a) => a.identity !== agent.identity && a.present);
  const links = (view.links || []).filter((l) => l.from === agent.identity && l.provenance && !l.provenance.includes("observed-spawn"));
  const roles = agent.roles || { role: [], team: [], task: [] };
  const roleInput = h("input", { type: "text", maxlength: "80", placeholder: "e.g. coordinator", "aria-label": "Role", list: "role-options" });
  const teamInput = h("input", { type: "text", maxlength: "80", placeholder: "team name", "aria-label": "Team" });
  const taskInput = h("input", { type: "text", maxlength: "80", placeholder: "task label", "aria-label": "Task" });
  const coordinator = h("select", { "aria-label": "Reports to" },
    h("option", { value: "", text: "choose an agent…" }),
    others.map((o) => h("option", { value: o.identity, text: `${displayName(o)} · ${o.workspace_label}` })));
  const subject = agent.identity;
  return h("details", { class: "card editor", "data-reading-key": "relations-editor" },
    h("summary", { text: "Declare role, team or reporting line" }),
    h("p", { class: "note", text: "Your declarations are stored with provenance \"manual\" and an audit history. Nothing is sent to the agent, and no role is ever assigned automatically." }),
    h("datalist", { id: "role-options" }, ["coordinator", "worker", "reviewer"].map((v) => h("option", { value: v }))),
    h("div", { class: "editor-row", "data-reading-key": "edit-role" }, roleInput,
      h("button", { class: "btn", type: "button", on: { click: (e) => e.currentTarget.parentNode.querySelector("input").value && relate({ action: "set", subject, kind: "role", value: e.currentTarget.parentNode.querySelector("input").value }) }, text: "Add role" })),
    roles.role.length ? h("div", { class: "chips" }, roles.role.map((r) => h("button", { class: "chip", type: "button",
      "aria-label": `Remove role ${r.value}`, on: { click: () => relate({ action: "clear", subject, kind: "role", value: r.value }) }, text: `${r.value} ✕` }))) : null,
    h("div", { class: "editor-row", "data-reading-key": "edit-team" }, teamInput,
      h("button", { class: "btn", type: "button", on: { click: (e) => e.currentTarget.parentNode.querySelector("input").value && relate({ action: "set", subject, kind: "team", value: e.currentTarget.parentNode.querySelector("input").value }) }, text: "Set team" }),
      roles.team.length ? h("button", { class: "btn quiet", type: "button", on: { click: () => relate({ action: "clear", subject, kind: "team" }) }, text: "Clear" }) : null),
    h("div", { class: "editor-row", "data-reading-key": "edit-task" }, taskInput,
      h("button", { class: "btn", type: "button", on: { click: (e) => e.currentTarget.parentNode.querySelector("input").value && relate({ action: "set", subject, kind: "task", value: e.currentTarget.parentNode.querySelector("input").value }) }, text: "Set task" }),
      roles.task.length ? h("button", { class: "btn quiet", type: "button", on: { click: () => relate({ action: "clear", subject, kind: "task" }) }, text: "Clear" }) : null),
    h("div", { class: "editor-row", "data-reading-key": "edit-coordinator" }, coordinator,
      h("button", { class: "btn", type: "button", on: { click: (e) => e.currentTarget.parentNode.querySelector("select").value && relate({ action: "link", subject, kind: "reports_to", object: e.currentTarget.parentNode.querySelector("select").value }) }, text: "Add" }),
      h("button", { class: "btn quiet", type: "button", title: "Replace every current coordinator with this one",
                    on: { click: (e) => e.currentTarget.parentNode.querySelector("select").value && relate({ action: "handover", subject, object: e.currentTarget.parentNode.querySelector("select").value }) }, text: "Hand over" })),
    links.length ? h("ul", { class: "plain" }, links.map((l) => h("li", {},
      `${KIND_WORDS[l.kind] || l.kind} ${l.hidden ? "hidden agent" : displayName(view.agents.find((a) => a.identity === l.to) || {})} `,
      l.hidden ? null : h("button", { class: "chip", type: "button", "aria-label": "Remove link",
        on: { click: () => relate({ action: "unlink", subject, kind: l.kind, object: l.to }) }, text: "remove ✕" })))) : null);
}

function relationHistory(detail) {
  const rows = (detail && detail.relation_history) || [];
  if (!rows.length) return h("p", { class: "muted small", text: detail ? "No declarations yet." : "Loading…" });
  return h("ul", { class: "timeline" }, rows.slice(0, 12).map((r) => h("li", {},
    h("div", { class: "when", text: `${clock(r.at)} · ${r.provenance} · ${r.actor}` }),
    h("div", { class: "what", text: `${r.action} ${r.kind}${r.value ? ` "${r.value}"` : ""}${r.hidden_end ? " (other agent hidden)" : r.other_name ? ` (${r.other_name})` : ""}${r.note ? ` · ${r.note}` : ""}` }))));
}

async function loadConversation(identity, limit, more) {
  const current = graph.conversation[identity] || { entries: [] };
  const before = more && current.before ? `&before=${current.before}` : "";
  graph.conversation[identity] = { ...current, loading: true, limit };
  render(true);
  try {
    const data = await api(`${UI.api.conversation}?identity=${encodeURIComponent(identity)}&limit=${limit}${before}`);
    const entries = more ? [...data.entries, ...current.entries] : data.entries;
    graph.conversation[identity] = { ...data, entries, limit, loading: false };
  } catch (err) {
    graph.conversation[identity] = { ...current, loading: false, error: err.message };
  }
  render(true);
}

function conversationCard(agent) {
  const conv = graph.conversation[agent.identity];
  const limit = (conv && conv.limit) || 10;
  const picker = h("select", { "aria-label": "How many messages" },
    [10, 25, 50].map((n) => { const o = h("option", { value: n, text: `last ${n}` }); o.selected = n === limit; return o; }));
  const output = app.outputs[agent.identity];
  return h("section", { class: "card", "data-reading-key": "conversation", "data-selectable": "output", "data-agent-identity": agent.identity },
    h("div", { class: "actions tight" },
      picker,
      h("button", { class: "btn", type: "button", on: { click: (e) => loadConversation(agent.identity, Number(e.currentTarget.parentNode.querySelector("select").value), false) },
                    text: conv ? "Reload messages" : "Show messages" }),
      agent.present ? h("button", { class: "btn", type: "button", on: { click: () => loadOutput(agent.identity) },
                                    text: output ? "Reload terminal" : "Show terminal lines" }) : null),
    h("p", { class: "note", text: "Read from the session trace on request: user and assistant text only (no reasoning, no tool calls). Nothing is sent to the agent." }),
    conv ? h("div", { class: "conversation" },
      conv.error ? h("p", { class: "error", text: conv.error }) : null,
      conv.available === false ? h("p", { class: "muted", text: `Unavailable: ${conv.reason}` }) : null,
      conv.before && !conv.loading ? h("button", { class: "btn quiet", type: "button", on: { click: () => loadConversation(agent.identity, limit, true) }, text: "Load older" }) : null,
      (conv.entries || []).map((e) => h("div", { class: `msg ${e.role}` },
        h("div", { class: "msg-head", text: `${e.role === "user" ? "User (as recorded)" : "Assistant"} · ${clock(e.at)}${e.redactions ? ` · ${e.redactions} redacted` : ""}` }),
        e.role === "user" ? h("div", { class: "msg-text", "data-selectable": "trace", "data-agent-identity": agent.identity }, fileNodes(e.text + (e.truncated ? " …" : "")))
          : h("div", { class: "msg-text md-msg", "data-selectable": "trace", "data-agent-identity": agent.identity }, mdBlock(e.text + (e.truncated ? " …" : ""))))),
      conv.loading ? h("p", { class: "muted", text: "Reading…" }) : null,
      conv.scan_limited ? h("p", { class: "note", text: "Stopped after reading 16 MB of the trace." }) : null) : null,
    output && !output.loading ? h("button", { class: "btn quiet", type: "button", on: { click: () => loadOutput(agent.identity, !output.raw) },
                                              text: output.raw ? "Show cleaned text" : "Show raw lines" }) : null,
    output ? [h("h3", { class: "sub", text: `Terminal (${output.source || "last lines"})${output.read_at ? ` · read ${clock(output.read_at)}` : ""}` }),
      h("pre", { class: "output", tabindex: "0", "data-selectable": "output", "data-agent-identity": agent.identity }, output.loading ? "Reading…" : output.error ? `Could not read: ${output.error}`
        : fileNodes(`${output.text || output.note || "(no output)"}${output.redactions ? `\n[${output.redactions} value(s) redacted]` : ""}`))] : null);
}

// "Runs on": service, model and effort, each with where it comes from. Unknown stays quiet.
function runsOn(agent) {
  const rt = agent.runtime || {}, icon = agent.icon || null;
  if (!agent.runtime && !icon) return null;
  // v0.9.7: the subscription on its own line, drawn by accountIcon(). With it, the service chip goes:
  // one mark per agent. The renderer's text label stands in when the account has no mark.
  const mark = icon ? accountIcon(icon, { size: 18 }) : null;
  const e = rt.effort, dots = effortDots(e);
  const where = (from) => from === "trace"
    ? (rt.reading ? RT.from_trace_reading : rtLabel("from_trace", { time: rt.trace_at ? clock(rt.trace_at) : "?" }))
    : RT.from_launch;
  const m = rt.model && rt.model.from, f = e && e.from;
  const source = rt.in_process && !m && !f ? RT.in_process
    : !m && !f ? RT.not_recorded
    : m && f && m === f ? rtLabel("source", { what: RT.model_and_effort, source: where(m) })
    : [m && rtLabel("source", { what: RT.model, source: where(m) }), f && rtLabel("source", { what: RT.effort, source: where(f) })].filter(Boolean).join(" ");
  return h("div", { class: "runs-on", "data-reading-key": "runs-on" },
    h("div", { class: "runs-line" },
      h("span", { class: "runs-label", text: RT.runs_on }),
      rt.service ? h("b", { class: "runs-service", text: rt.service.label }) : null,
      rt.model ? h("span", { class: "runs-model", title: rt.model.id, text: rt.model.label }) : h("span", { class: "runs-unknown", text: RT.model_unknown }),
      e ? h("span", { class: "runs-effort", title: effortSays(e) },
            dots ? h("span", { class: "pips", role: "img", "aria-label": effortSays(e) },
              Array.from({ length: e.steps }, (_, i) => h("i", { class: i < e.level ? "on" : "" }))) : null,
            h("span", { "aria-hidden": dots ? "true" : null, text: dots ? e.label : effortSays(e) }))
        : h("span", { class: "runs-unknown", text: RT.effort_unknown })),
    mark ? h("div", { class: "runs-line runs-account", "data-reading-key": "runs-account" },
      h("span", { class: "runs-label", text: RT.account }), mark,
      mark.classList.contains("acct-text") ? null : h("b", { class: "runs-account-name", text: icon.label })) : null,
    // v0.9.7.1: the account says where it comes from (launch record, config folder, environment, parent).
    h("p", { class: "metric-meta runs-source", text: [source, icon ? rtLabel("account_from",
      { source: icon.source?.label || UI.account_icons.labels.source_launch }) : ""].filter(Boolean).join(" ") }));
}

// The agent sheet itself (inspector) is drawn by details.js, the same component as the agent page.
