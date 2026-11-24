// One agent, drawn the same in the graph sheet and on the full page.
// Three levels: the status card, "Needs you" only when something is open, and four doors to the
// extra screens (Status, Activity, Usage, Links). All agent text is set with textContent.
"use strict";

const DETAIL_SCREENS = [["status", "Status"], ["activity", "Activity"], ["usage", "Usage"], ["links", "Links"]];
const SCREEN_NAME = Object.fromEntries(DETAIL_SCREENS);
const ACTIVITY_ROWS = 10;
const RIBBON_HOURS = 24;
const CHECK_SETTLE_MS = 400;

// ---------------------------------------------------------------- which screen

// The sheet keeps its screen locally and starts on the main screen for every new node;
// the page takes it from the route (#/a/<id>/usage). Old deep links land on their screen.
function detailScreen(a, sheet) {
  if (sheet) return app.sheetScreen?.identity === a.identity ? app.sheetScreen.screen : null;
  const section = route().section;
  if (section === "conversation" || section === "output") return "activity";
  if (section === "report") return "status";
  return SCREEN_NAME[section] ? section : null;
}

function openScreen(a, screen, sheet) {
  if (!sheet) { location.hash = screen ? hashLink(screen, a.identity) : hashLink("agent", a.identity); return; }
  app.sheetScreen = screen ? { identity: a.identity, screen } : null;
  graphRefresh(true);
  if (graph.shell) graph.shell.sheetBody.scrollTop = 0;
}

// Escape on an extra screen goes back to the main screen first.
function closeDetailScreen() {
  const r = route();
  if (r.name === "graph" && app.sheetScreen && app.sheetScreen.identity === r.id) { openScreen({ identity: r.id }, null, true); return true; }
  if (r.name === "agent" && SCREEN_NAME[detailScreen({ identity: r.id }, false)]) { location.hash = hashLink("agent", r.id); return true; }
  return false;
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented || ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
  if (app.composer || document.querySelector(".term-sheet:not([hidden])")) return;
  if (closeDetailScreen()) { e.preventDefault(); e.stopImmediatePropagation(); }
}, true);

// ---------------------------------------------------------------- the two places

function renderAgent(r) {
  const fromView = app.view.agents.find((a) => a.identity === r.id);
  const detail = app.details[r.id];
  if (!detail || (now() - (detail.generated_at || 0)) * 1000 > DETAIL_MS) loadDetailSoon(r.id);
  const a = fromView ? { ...detail?.agent, ...fromView } : null;
  if (!a) {
    return h("div", {}, h("a", { class: "back", href: hashLink("list"), text: "Workspaces" }),
      h("section", { class: "card empty" }, h("h1", { text: "Not available" }),
        h("p", { class: "muted", text: "This agent is gone or not in a watched workspace." })));
  }
  return h("div", { "data-reading-key": `agent:${r.id}` },
    h("a", { class: "back", href: hashLink("workspace", a.workspace_id),
      text: [a.workspace_label, a.tab_label].filter(Boolean).join(" › ") }),
    agentDetails(a, { sheet: false }));
}

function inspector(agent) {
  const detail = app.details[agent.identity];
  if (!detail || (now() - (detail.generated_at || 0)) * 1000 > DETAIL_MS) loadDetailSoon(agent.identity);
  return h("div", { class: "inspector-body", "data-reading-key": `inspector:${agent.identity}` }, agentDetails(agent, { sheet: true }));
}

function agentDetails(a, { sheet = false } = {}) {
  const detail = app.details[a.identity];
  const screen = detailScreen(a, sheet);
  const main = h("div", { class: "details-main", "data-reading-key": "details-main" },
    statusCard(a, detail, sheet), needsBlock(a, detail), actionBar(a, sheet), doors(a, detail, sheet));
  if (sheet && screen) return h("div", { class: "details is-sheet", "data-reading-key": `details:${a.identity}:${screen}` }, screenView(a, detail, screen, sheet));
  return h("div", { class: `details${sheet ? " is-sheet" : " is-page"}${screen ? " has-screen" : ""}`, "data-reading-key": `details:${a.identity}` },
    main, screen ? screenView(a, detail, screen, sheet) : null);
}

function screenView(a, detail, screen, sheet) {
  const body = { status: statusScreen, activity: activityScreen, usage: usageScreen, links: linksScreen }[screen](a, detail, sheet);
  return h("section", { class: "details-screen", "data-screen": screen, "data-reading-key": `screen:${screen}` },
    h("div", { class: "screen-bar" },
      h("button", { class: "btn quiet screen-back", type: "button", "aria-label": `Back to ${displayName(a)}`,
        on: { click: () => openScreen(a, null, sheet) } }, h("span", { "aria-hidden": "true", text: "‹" })),
      h("i", { class: `dot state s-${detailHue(a)}`, "aria-hidden": "true" }),
      h("span", { class: "screen-who", text: displayName(a) }), h("span", { class: "screen-name", text: ` · ${SCREEN_NAME[screen]}` })),
    body);
}

// ---------------------------------------------------------------- status card

// The colour of the stripe, the pill and the screen-bar dot: state only.
function detailHue(a) {
  if (a.quota_stop || a.state === "out_of_quota") return "out_of_quota";
  if (a.kind === "stall" || a.kind === "failure") return "caution";
  const items = askItems(a);
  if (items.length && !openAskItems(a).length && !atQuestionScreen(a)) return a.state === "needs_you" ? "waiting" : a.state;
  if (agentTier(a) === "waiting_on_you") return "needs_you";
  return a.state === "needs_you" ? "waiting" : STATES.includes(a.state) ? a.state : "unknown";
}

function detailPillText(a) {
  if (agentTier(a) === "waiting_on_you" && !(askItems(a).length && !openAskItems(a).length)) {
    const kind = NEEDS.includes(a.kind) && a.label !== tierHead("waiting_on_you") ? a.label : null;
    return kind || tierHead("waiting_on_you");
  }
  if (a.quota_stop) return a.label || "Out of quota";
  if (NEEDS.includes(a.kind) || a.kind === "stall" || a.awaiting_user) return a.label || a.state_label;
  return a.state_label;
}

// "2h56" in this state; "last seen 3h ago" when it is gone or Herdr is not connected.
function detailWhen(a) {
  if (!a.present) return `gone · last seen ${ago(a.last_seen)} ago`;
  if (!app.view.collector?.herdr?.connected) return `last seen ${ago(a.last_seen)} ago`;
  return since(a);
}

// The last thing it said: the AI headline, the asks summary, its own last line. A question on
// screen is already in "Needs you", so its text is not repeated here.
function nowLine(a, detail) {
  const s = a.summary, asks = asksOf(a);
  if (s && s.status === "complete" && s.current && s.headline) return s.headline;
  if (asks?.summary && asks.state !== "maybe") return asks.summary;
  if (a.snippet && !a.question && !openAskItems(a).length) return a.snippet;
  return detail?.fallback?.available && !a.question ? detail.fallback.text : "";
}

function statusCard(a, detail, sheet) {
  const said = nowLine(a, detail);
  const reports = a.reports_to;
  return h("section", { class: `card status-card s-${detailHue(a)}`, "data-reading-key": "status-card" },
    h("div", { class: "sc-head" },
      h("div", { class: `avatar s-${detailHue(a)}`, "aria-hidden": "true" }, initials(a), avatarCorner(a)),
      h(sheet ? "h2" : "h1", { class: "sc-name", text: displayName(a) }),
      watchCloselyButton(a, { text: !sheet })),
    h("p", { class: "sc-state" }, h("span", { class: `pill state s-${detailHue(a)}`, text: detailPillText(a) }),
      h("span", { class: "sc-since", text: detailWhen(a) })),
    // What a quiet agent is waiting for (flow.py, 0.9.10.0), in the colour of its ring in the graph.
    a.waiting_for ? h("p", { class: `sc-waiting w-${a.waiting_for.kind}`, title: WAIT_SAYS[a.waiting_for.kind] || "" },
      h("i", { class: "dot", "aria-hidden": "true" }), waitingSays(a)) : null,
    detailBanner(a),
    a.title ? h("p", { class: "sc-task", text: a.title }) : h("p", { class: "sc-task none", text: "no task title" }),
    said ? h("p", { class: "sc-now" }, mdLine(said, UI.ask_items.cut.line)) : null,
    settingChips(a, sheet),
    reports ? h("p", { class: "sc-reports" }, h("span", { class: "muted small", text: "reports to " }),
      reports.identity ? h("a", { class: "chip", href: hashLink(sheet ? "graph" : "agent", reports.identity) },
        h("i", { class: `dot state s-${agentById(reports.identity)?.state || "unknown"}` }), reports.name, " ›") : h("span", { class: "chip", text: reports.name })) : null);
}

// One line, caution tint, only for the rare cases.
function detailBanner(a) {
  const wall = a.quota_wall;
  const text = !a.present ? "No longer running. These are its last known details."
    : wall ? `Out of quota${wall.provider ? ` (${wall.provider})` : ""}${wall.until ? ` · resets in ${duration(wall.until - now())} (${clock(wall.until)})` : ""}. Routing skips it; no automatic restart.`
    : null;
  return text ? h("p", { class: "sc-banner", role: "note", "data-reading-key": "sc-banner", text }) : null;
}

function contextLevel(percent) {
  return percent >= 85 ? "full" : percent >= 60 ? "mid" : "ok";
}

function accountQuota(a) {
  const id = a.icon?.account;
  return id ? (app.view.quotas?.accounts || []).find((q) => q.icon?.account === id || q.id === id) || null : null;
}

// Account, model with effort, context, folder, sandbox: one row of chips.
function settingChips(a, sheet) {
  const rt = a.runtime || {}, c = a.metrics?.context, dots = effortDots(rt.effort);
  const usage = () => openScreen(a, "usage", sheet);
  const quota = accountQuota(a), hot = quota && quota.worst_pct >= 85 ? quota : null;
  const chips = [
    a.icon ? h("button", { class: "chip set-chip", type: "button", title: `${serviceSays(a)} · account from ${a.icon.source?.label || UI.account_icons.labels.source_launch}`, on: { click: usage } },
      accountIcon(a.icon, { size: 16, text: false }), h("span", { text: a.icon.label }),
      hot ? h("span", { class: `chip-quota q-${hot.level}`, title: `${quotaName(hot)}: ${hot.worst_pct}% used`, text: `${hot.worst_pct}%` }) : null) : null,
    rt.model || rt.service ? h("button", { class: "chip set-chip", type: "button", title: runtimeSays(a), on: { click: usage } },
      h("span", { text: rt.badge?.text || rt.model?.label || rt.service?.label }),
      dots ? h("span", { class: "pips", role: "img", "aria-label": effortSays(rt.effort) },
        Array.from({ length: rt.effort.steps }, (_, i) => h("i", { class: i < rt.effort.level ? "on" : "" })))
        : rt.effort ? h("span", { class: "muted", text: rt.effort.label }) : null) : null,
    c?.available && c.window ? h("button", { class: `chip set-chip ctx-chip lv-${contextLevel(c.percent)}`, type: "button",
      title: `Context ${tokens(c.value)} of ${tokens(c.window)} tokens`, on: { click: usage } }, `ctx ${Math.round(c.percent)}%`) : null,
    a.cwd ? h("span", { class: "chip set-chip folder-chip", title: a.cwd, text: shortPath(a.cwd) }) : null,
    a.launch?.sandbox ? h("span", { class: "chip set-chip sandbox-chip", title: `Sandbox: ${a.launch.sandbox}`, text: "🔒 sandbox" }) : null,
  ].filter(Boolean);
  return chips.length ? h("div", { class: "sc-chips", "data-reading-key": "sc-chips" }, chips) : null;
}

// ---------------------------------------------------------------- needs you

// The rows, in the order of the brief: terminal dialog, question items, "may want something",
// finished, stall or failure, out of quota. Nothing open: no block at all.
function needsBlock(a, detail) {
  const rows = [], s = asksOf(a), done = sessionAnswers(a);
  let open = 0;
  const q = a.question && !askItems(a).length ? a.question : null;
  if (q && a.present !== false) { rows.push(dialogRow(a, q)); open += 1; }
  else if (app.dialogAnswers?.[a.identity] && !a.question) rows.push(tickRow(`dialog:${a.identity}`, app.dialogAnswers[a.identity]));
  for (const item of askItems(a)) {
    if (askOpen(item)) { rows.push(needsAskRow(a, item)); open += 1; }
    else if (done.has(item.id)) rows.push(tickRow(`ask:${item.id}`, askAnswer(item)));
  }
  const read = canMarkAgentRead(a);
  if (s?.state === "maybe") { rows.push(maybeRow(a, s)); open += 1; }
  else if (a.quota_stop) { rows.push(quotaRow(a)); open += 1; }
  else if (a.event_id && !a.acked && !q && !askItems(a).length) {
    // Any other open item that is Alex's to look at (not a coordinator's pick-up): finished, a stall or
    // failure, a question Herdr could not read. One line and a check, or the hint saying how it clears.
    const trouble = a.kind === "stall" || a.kind === "failure";
    const mine = trouble || ["fyi_finished", "waiting_on_you", "might_look"].includes(agentTier(a));
    if (mine && (read || readHint(a) || trouble)) {
      rows.push(s?.state === "none" || a.kind === "completion" || a.state === "done" ? finishedRow(a, s) : lookRow(a, trouble));
      open += 1;
    }
  }
  // An open question can also be cleared without answering here (answered in the terminal, say).
  const clear = open && read && (q || openAskItems(a).length) && !rows.some((row) => row.querySelector(".btn-check"))
    ? h("div", { class: "needs-clear" }, readCheck(a, "Mark read")) : null;
  const pinned = pinnedQuestions(a);
  if (pinned) open += pinned.count;
  if (!rows.length && !pinned) return null;
  const err = app.ackErrors[a.identity];
  return h("section", { class: `card needs-block${open ? "" : " all-done"}`, "data-reading-key": "needs-block", "aria-live": "polite" },
    h("h2", { class: "needs-head", text: open ? `Needs you · ${open}` : rows.length ? "✓ All answered" : "Needs you" }),
    h("ol", { class: "needs-list" }, rows),
    clear,
    pinned?.node,
    s?.state === "questions" && askItems(a).length ? askOriginal(a, s) : null,
    err ? h("p", { class: "error", role: "alert", text: `Not marked read: ${err}` }) : null);
}

// Items Alex answered from this page in this session: they stay as a tick row until the next visit.
function sessionAnswers(a) {
  return new Set(askItems(a).filter((i) => app.askAnswers?.[i.id] && app.askAnswers[i.id].delivery !== "failed").map((i) => i.id));
}

function tickMark() {
  return svgNode("svg", { class: "tick", viewBox: "0 0 24 24", "aria-hidden": "true" }, svgNode("path", { d: "M5 12.5l4.5 4.5L19 7.5", pathLength: 1 }));
}

function tickRow(key, answer) {
  return h("li", { class: "needs-row is-done", "data-reading-key": key },
    h("p", { class: "tick-line", role: "status" }, tickMark(), h("span", { text: repliedText(answer) }),
      answer.delivery === "queued" ? h("span", { class: "ask-queued", text: ` ${UI.ask_items.labels.queued}` }) : null));
}

// `extra` says something `base` does not: three or more new words (not just a "Decision for Alex:" label).
function addsWords(extra, base) {
  const words = (t) => mdPlain(t || "").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((w) => w.length > 2);
  const have = new Set(words(base));
  return words(extra).filter((w) => !have.has(w)).length >= 3;
}

function needsAskRow(a, item) {
  const done = askAnswer(item), failed = done?.delivery === "failed" ? done : null;
  const showContext = item.context && addsWords(item.context, item.text);
  const showQuote = item.quote && addsWords(item.quote, `${item.text} ${item.context || ""}`);
  const to = item.answer_to;
  const elsewhere = to?.identity && to.identity !== a.identity;
  return h("li", { class: "needs-row ask-item", "data-reading-key": `ask:${item.id}`, "data-agent-identity": a.identity },
    h("p", { class: "ask-text" }, mdLine(item.text, 0, true),
      item.urgency === "later" ? h("span", { class: "ask-later", title: UI.ask_items.labels.for_later_title, text: UI.ask_items.labels.for_later }) : null),
    showContext ? h("p", { class: "ask-context" }, mdLine(item.context, 0, true)) : null,
    showQuote ? h("blockquote", { class: "ask-quote", title: UI.ask_items.labels.quote },
      h("span", { class: "sr-only", text: `${UI.ask_items.labels.quote}: ` }),
      mdBlock(item.quote, { limit: UI.ask_items.cut.card, place: `quote:${item.id}` })) : null,
    elsewhere ? h("p", { class: "ask-route", text: askText(to.where ? "route_where" : "route",
      { asker: askSideName(item.asked_by, displayName(a)), to: askSideName(to, to.name), where: to.where || "" }) }) : null,
    askControls(a, item, failed));
}

// A terminal dialog (approval or question on screen): its own choices; tap one, then Send.
function dialogRow(a, q) {
  app.dialogChoice ||= {};
  const labels = UI.easy_reply.labels, chosen = app.dialogChoice[q.id] || null, sending = !!app.replySending?.[q.id];
  const options = q.terminal && q.options?.length ? q.options : [...new Set([...(q.options || []), labels.yes, labels.no])];
  const send = async () => {
    if (!chosen) return;
    if (await sendQuestionReply(q, chosen)) {
      (app.dialogAnswers ||= {})[a.identity] = { text: chosen, at: now(), delivery: "sent" };
      delete app.dialogChoice[q.id];
      render(true);
    }
  };
  return h("li", { class: "needs-row dialog-row", "data-reading-key": `question:${q.id}`, "data-agent-identity": a.identity },
    h("p", { class: "ask-text" }, mdLine(q.text, 0, true)),
    q.reason ? h("p", { class: "muted small", text: q.reason }) : null,
    questionImage(a, q),
    h("div", { class: "ask-choices", role: "radiogroup", "aria-label": UI.ask_items.labels.choices },
      options.map((option) => h("button", { class: "btn ask-choice", type: "button", role: "radio", "aria-checked": String(chosen === option),
        disabled: sending, text: option, on: { click: () => { app.dialogChoice[q.id] = chosen === option ? null : option; render(true); } } }))),
    q.terminal ? h("p", { class: "muted small terminal-hint", text: UI.question_preview.labels.choose }) : null,
    h("div", { class: "actions tight" },
      h("button", { class: "btn primary ask-send", type: "button", disabled: sending || !chosen, on: { click: send },
        text: sending ? UI.ask_items.labels.sending : chosen ? askText("send_choice", { choice: chosen }) : UI.ask_items.labels.send }),
      h("button", { class: "btn btn-write", type: "button", on: { click: () => draftQuestionReply(q) } }, icon("chat", "btn-icon"), labels.other)),
    replySlot(`question:${q.id}`, a));
}

function maybeRow(a, s) {
  return h("li", { class: "needs-row maybe-row", "data-reading-key": `maybe:${a.identity}` },
    h("p", { class: "ask-text", text: UI.ask_items.labels.maybe }),
    h("blockquote", { class: "ask-quote" }, mdBlock(s.original, { limit: UI.ask_items.cut.card, place: `maybe:${a.identity}` })),
    h("div", { class: "actions tight" }, easyReplyButton(a, { anchor: `maybe:${a.identity}`, primary: false }),
      readCheck(a, "Nothing needed")),
    replySlot(`maybe:${a.identity}`, a));
}

function finishedRow(a, s) {
  return h("li", { class: "needs-row finished-row", "data-reading-key": `finished:${a.event_id}` },
    h("div", { class: "check-line" },
      h("p", { class: "check-text" }, h("b", { text: "Finished: " }), a.title || a.label || "a turn",
        a.event_at ? h("span", { class: "muted", text: ` · ${ago(a.event_at)} ago` }) : null),
      readCheck(a, "Mark read")),
    s?.state === "none" ? askOriginal(a, s) : null);
}

function lookRow(a, trouble) {
  return h("li", { class: "needs-row look-row", "data-reading-key": `look:${a.event_id}` },
    h("div", { class: "check-line" },
      h("p", { class: `check-text${trouble ? " caution-text" : ""}` }, h("b", { text: `${a.label || "Needs a look"}: ` }), a.reason || a.evidence?.note || a.snippet || ""),
      readCheck(a, "Mark read")));
}

function quotaRow(a) {
  const wall = a.quota_wall;
  const key = `quota:${a.identity}`;
  return h("li", { class: "needs-row quota-row-needs", "data-reading-key": key },
    h("div", { class: "check-line" },
      h("p", { class: "check-text caution-text", text: `Out of quota${wall?.until ? ` until ${clock(wall.until)}` : ""}` }),
      checkButton(key, "Got it", async () => {
        await api(UI.api.quota_stops_ack, { method: "POST", body: { identities: [a.identity] } });
      }, async () => { await refresh(true); })));
}

// Mark read as a round check. It fills and draws its tick only after the server confirmed,
// then closes or goes back as ack() always did.
function readCheck(a, label) {
  if (!canMarkAgentRead(a)) { const hint = readHint(a); return hint ? h("span", { class: "muted small read-state-hint", text: hint }) : null; }
  const key = `read:${a.event_id}`;
  return checkButton(key, label, null, null, () => ack(a.event_id, a.identity, { settle: () => settleCheck(key) }));
}

function settleCheck(key) {
  (app.checked ||= new Set()).add(key);
  const button = document.querySelector(`[data-reading-key="${CSS.escape(key)}"]`);
  if (button) { button.classList.add("is-done"); button.setAttribute("aria-busy", "false"); }
  return new Promise((ok) => setTimeout(ok, CHECK_SETTLE_MS));
}

// run: the request (throws on failure); after: what happens once the tick is drawn.
// own: a function that does request, tick and navigation itself (ack()).
function checkButton(key, label, run, after, own = null) {
  app.checked ||= new Set();
  const busy = app.markingRead.has(key), done = app.checked.has(key);
  return h("button", { class: `btn-check${done ? " is-done" : ""}`, type: "button", "data-reading-key": key, "aria-label": label,
    title: label, disabled: busy || done, "aria-busy": String(busy),
    on: { click: async (event) => {
      event.preventDefault(); event.stopPropagation();
      if (app.markingRead.has(key) || app.checked.has(key)) return;
      app.markingRead.add(key);
      event.currentTarget.disabled = true;
      try {
        if (own) await own();
        else {
          await run();
          await settleCheck(key);
          await after?.();
          app.checked.delete(key);
        }
      } catch (err) { flash(`${label}: ${err.message}`); }
      finally { app.markingRead.delete(key); render(true); }
    } } },
    h("span", { class: "check-ring", "aria-hidden": "true" }, tickMark()), h("span", { class: "check-label", text: label }));
}

// Pinned (watched) questions stay in this block: the agent's own, and on a coordinator's page its
// team's, under each worker's name. Ones already drawn above (dialog, question items) are skipped.
function pinnedQuestions(a) {
  const ids = new Set([a.identity, ...(a.is_coordinator ? teamOf(a).map((w) => w.identity) : [])]);
  const questions = (app.view.questions || []).filter((q) => q.keep_open && ids.has(q.identity)
    && !(q.identity === a.identity && (askItems(a).length || q.id === a.question?.id)));
  if (!questions.length) return null;
  const view = { ...app.view, questions, agents: app.view.agents.filter((x) => ids.has(x.identity)) };
  const entries = questionEntries(view);
  return entries.length ? { count: openQuestionCount(entries),
    node: h("div", { class: "watched-questions", "data-reading-key": `watched-questions:${a.identity}` }, entries.map(questionEntryCard)) } : null;
}

// ---------------------------------------------------------------- action bar and doors

function waitsOnAlex(a) {
  return agentTier(a) === "waiting_on_you" && !openAskItems(a).length && !(a.question && !askItems(a).length) && asksOf(a)?.state !== "maybe";
}

function actionBar(a, sheet) {
  const maybe = asksOf(a)?.state === "maybe";
  return h("div", { class: "details-actions", "data-reading-key": "details-actions" },
    h("div", { class: "actions" },
      maybe ? null : easyReplyButton(a, { question: a.question, anchor: `agent:${a.identity}`, primary: waitsOnAlex(a) }),
      terminalButton(a),
      openInHerdrButton(a),
      sheet ? h("a", { class: "btn btn-go", href: hashLink("agent", a.identity), text: "Page" })
        : h("a", { class: "btn btn-go", href: hashLink("graph", a.identity), text: "Graph" })),
    replySlot(`agent:${a.identity}`, a));
}

function doors(a, detail, sheet) {
  const values = { status: statusDoor(a, detail), activity: activityDoor(a, detail), usage: usageDoor(a), links: linksDoor(a) };
  return h("nav", { class: "doors", "aria-label": "More about this agent", "data-reading-key": "doors" },
    DETAIL_SCREENS.map(([screen, name]) => {
      const [value, flag] = values[screen];
      return h("button", { class: "door", type: "button", "data-door": screen, on: { click: () => openScreen(a, screen, sheet) } },
        h("span", { class: "door-name" }, name, flag ? h("i", { class: `dot state s-${flag[0]}`, title: flag[1] }) : null,
          h("span", { class: "door-chev", "aria-hidden": "true", text: "›" })),
        h("span", { class: "door-value", text: value }));
    }));
}

function statusDoor(a, detail) {
  const report = a.status_report?.summary;
  if (a.is_coordinator && report) return [`${(report.done || []).length} done · ${(report.left || []).length} left`, report.stuck?.length ? ["caution", "Stuck workers"] : null];
  if (a.is_coordinator) { const team = teamCounts(a); return [team.text || "no team yet", null]; }
  const s = detail?.summary || a.summary;
  if (s?.status === "complete") return ["AI summary", null];
  if (s?.status === "queued" || s?.status === "running") return ["summarizing…", null];
  if (s?.status === "failed") return ["summary failed", ["caution", "Summary failed"]];
  return [detail?.fallback?.available ? "last message" : "why this state", null];
}

function activityDoor(a, detail) {
  if (!a.status_since) return [detail?.activity?.length ? `${detail.activity.length} changes` : "nothing yet", null];
  return [`${a.state_label} since ${clock(a.status_since)}`, null];
}

function usageDoor(a) {
  const m = a.metrics || {}, c = m.context;
  const parts = [m.tokens?.available ? `${tokens(m.tokens.value)} tok` : null, c?.available && c.window ? `${Math.round(c.percent)}%` : null].filter(Boolean);
  return [parts.join(" · ") || "not recorded", c?.available && c.window && c.percent >= 85 ? ["caution", "Context nearly full"] : null];
}

function linksDoor(a) {
  const groups = linkGroups(a), cycle = groups.all.some((row) => row.cycle);
  const live = groups.workers.filter((w) => w.live).length, gone = groups.workers.length - live;
  const text = groups.workers.length ? `${live} worker${live === 1 ? "" : "s"}${gone ? ` · ${gone} gone` : ""}`
    : a.reports_to ? `reports to ${a.reports_to.name}` : groups.all.length ? `${groups.all.length} link${groups.all.length === 1 ? "" : "s"}` : "none";
  return [text, cycle ? ["caution", "Part of a reporting cycle"] : null];
}

// ---------------------------------------------------------------- links (one row per agent)

const LINK_VERB = {
  "reports_to:out": "reports to", "reports_to:in": "coordinates",
  "spawned_by:out": "spawned by", "spawned_by:in": "spawned",
  "forked_from:out": "forked from", "forked_from:in": "forked into",
};

function linkGroups(a) {
  const rows = new Map();
  for (const l of app.view.links || []) {
    if (l.from !== a.identity && l.to !== a.identity) continue;
    const out = l.from === a.identity, other = out ? l.to : l.from;
    const hidden = out ? l.hidden : l.from_hidden;
    const key = hidden ? `hidden:${l.label || other}` : other;
    const row = rows.get(key) || { identity: hidden ? null : other, hidden, label: l.label, name: out ? l.to_name : l.from_name, verbs: [], links: [], cycle: false };
    row.verbs.push(`${l.kind}:${out ? "out" : "in"}`);
    row.links.push(l);
    row.cycle ||= !!l.cycle;
    rows.set(key, row);
  }
  const groups = { up: [], workers: [], spawnedBy: [], forks: [], all: [...rows.values()] };
  for (const row of rows.values()) {
    const other = row.identity ? agentById(row.identity) : null;
    row.agent = other;
    row.live = !!other?.present;
    row.text = row.verbs.map((v) => LINK_VERB[v] || v.split(":")[0]).join(", ");
    row.action = row.links.map(actionSays).filter(Boolean).join(" · ");   // last message on the line (0.9.10.0)
    if (row.verbs.includes("reports_to:out")) groups.up.push(row);
    else if (row.verbs.includes("reports_to:in") || row.verbs.includes("spawned_by:in")) groups.workers.push(row);
    else if (row.verbs.includes("spawned_by:out")) groups.spawnedBy.push(row);
    else groups.forks.push(row);
  }
  const order = (row) => (row.agent ? STATES.indexOf(row.agent.state) : 99);
  groups.workers.sort((x, y) => order(x) - order(y) || linkName(x).localeCompare(linkName(y)));
  return groups;
}

function linkName(row) {
  return row.hidden ? row.label || "hidden or unknown agent" : row.agent ? displayName(row.agent) : row.name || "unknown agent";
}

function linkSource(row) {
  return row.links.map((l) => `${LINK_VERB[`${l.kind}:${l.from === row.identity ? "in" : "out"}`] || l.kind}: ${l.source}`
    + `${l.inferred ? ` · confidence ${Math.round(l.confidence * 100)}%` : ""}${l.since ? ` · since ${clock(l.since)}` : ""}`).join("; ");
}

function teamOf(a) {
  return linkGroups(a).workers.filter((row) => row.agent).map((row) => row.agent);
}

function teamCounts(a) {
  const workers = linkGroups(a).workers, counts = {};
  for (const row of workers) { const key = row.live ? row.agent.state : "gone"; counts[key] = (counts[key] || 0) + 1; }
  const order = [...STATES, "gone"].filter((k) => counts[k]);
  return { counts, order, total: workers.length,
    text: order.map((k) => `${counts[k]} ${k === "gone" ? "gone" : STATE_SHORT[k] || k}`).join(" · ") };
}

function teamBar(a) {
  const team = teamCounts(a);
  if (!team.total) return null;
  return h("div", { class: "team-bar", "data-reading-key": "team-bar" },
    h("div", { class: "bar", role: "img", "aria-label": team.text },
      team.order.map((k) => h("span", { class: k === "gone" ? "is-gone" : `s-${k}`, style: { flex: String(team.counts[k]) }, title: `${team.counts[k]} ${k}` }))),
    h("p", { class: "muted small", text: team.text }));
}

function linkRow(a, row, sheet) {
  const name = row.identity && row.agent
    ? h("a", { class: "chip", href: hashLink(sheet ? "graph" : "agent", row.identity) }, h("i", { class: `dot state s-${row.live ? row.agent.state : "parked"}` }), linkName(row))
    : h("span", { class: "unknown-val", text: linkName(row) });
  const cross = row.agent && row.agent.workspace_id !== a.workspace_id ? ` (in ${row.agent.workspace_label})` : "";
  return h("li", { "data-reading-key": `link:${row.identity || row.label}` },
    h("details", { class: "link-row" },
      h("summary", {}, name, h("span", { class: "muted small", text: ` · ${row.text}${cross}` }),
        row.action ? h("span", { class: "link-action small", text: row.action }) : null),
      h("p", { class: "metric-meta", text: linkSource(row) })),
    row.cycle ? h("p", { class: "warn small", text: "Part of a reporting cycle" }) : null);
}

function linksScreen(a, detail, sheet) {
  const g = linkGroups(a), roles = a.roles || { role: [], team: [], task: [] };
  const live = g.workers.filter((w) => w.live), gone = g.workers.filter((w) => !w.live);
  const declared = ["role", "team", "task"].flatMap((kind) => (roles[kind] || []).map((r) => h("span", { class: "chip",
    title: `${r.provenance}, since ${clock(r.since)}`, text: `${kind}: ${r.value}` })));
  const group = (title, rows) => rows.length ? [h("h3", { class: "sub", text: title }), h("ul", { class: "plain link-list" }, rows.map((row) => linkRow(a, row, sheet)))] : null;
  return h("div", { class: "links-screen" },
    declared.length ? h("div", { class: "chips", "data-reading-key": "roles" }, declared) : null,
    group("Reports to", g.up),
    g.workers.length || a.finished_subagents?.length ? [h("h3", { class: "sub", text: `Coordinates · ${g.workers.length}` }),
      live.length ? h("div", { class: "chips worker-chips", "data-reading-key": "workers" }, live.map((row) =>
        h("a", { class: `chip${row.links.some((l) => l.waiting) ? " is-awaited" : ""}`, href: hashLink(sheet ? "graph" : "agent", row.identity),
          title: [row.text, row.action, linkSource(row)].filter(Boolean).join(" · ") },
          h("i", { class: `dot state s-${row.agent.state}` }), linkName(row)))) : null,
      live.length ? h("details", { class: "fold", "data-reading-key": "workers-how" }, h("summary", { text: "How these links are known" }),
        h("ul", { class: "plain link-list" }, live.map((row) => h("li", { class: "metric-meta" }, h("b", { text: `${linkName(row)} · ${row.text}: ` }), linkSource(row),
          row.action ? h("span", { class: "link-action", text: ` · ${row.action}` }) : null))),
        live.some((row) => row.cycle) ? h("p", { class: "warn small", text: "Part of a reporting cycle" }) : null) : null,
      g.workers.filter((row) => row.cycle && row.live).map((row) => h("p", { class: "warn small", text: `${linkName(row)}: part of a reporting cycle` })),
      gone.length ? h("details", { class: "fold", "data-reading-key": "workers-gone" }, h("summary", { text: `${gone.length} gone` }),
        h("ul", { class: "plain gone-list" }, gone.map((row) => h("li", { class: "muted small", title: linkSource(row) }, `${linkName(row)} · ${row.text}`)))) : null,
      subagentFold(a)] : null,
    group("Spawned by", g.spawnedBy),
    group("Forked", g.forks),
    !g.all.length ? h("p", { class: "muted small", text: "No links known; no declaration or reporting evidence." }) : null,
    detail?.same_tab?.length ? [h("h3", { class: "sub", text: "Same tab" }),
      h("div", { class: "chips" }, detail.same_tab.map((p) => h("a", { class: "chip", href: hashLink(sheet ? "graph" : "agent", p.identity), text: p.name || p.agent }))),
      h("p", { class: "note", text: reportingLineNote(app.view, a.workspace_id) })] : null,
    relationsEditor(a, app.view),
    h("details", { class: "fold", "data-reading-key": "relation-history" }, h("summary", { text: "Relationship history" }), relationHistory(detail)));
}

// ---------------------------------------------------------------- status

// count: the size of the whole list when only some of its rows are shown.
function block(kind, title, items, extra = null, count = items?.length) {
  if (!items?.length && !extra) return null;
  return h("section", { class: `report-block rb-${kind}`, "data-reading-key": `block:${title}` },
    h("h3", { class: "rb-head" }, title, count ? h("span", { class: "rb-count", text: String(count) }) : null),
    items?.length ? h("ul", {}, items) : null, extra);
}

function aiTools(...buttons) {
  return h("div", { class: "ai-tools", title: "Uses your summary settings. Messages open as editable drafts." }, buttons);
}

function statusScreen(a, detail, sheet) {
  return h("div", { class: "status-screen" },
    a.is_coordinator ? coordinatorStatus(a, sheet) : null,
    summaryBlocks(a, detail),
    h("details", { class: "fold", "data-reading-key": "why-state" }, h("summary", { text: "Why this state" }),
      h("ul", { class: "evidence" }, evidenceLines(a).map((line) => evidenceItem(line, a)))),
    a.read_action?.reason ? h("p", { class: "muted small read-state-hint", text: a.read_action.reason }) : null);
}

function coordinatorStatus(a, sheet) {
  const row = a.status_report, report = row?.summary, labels = UI.watch_closely.labels;
  const ids = new Set(report?.members || teamOf(a).map((w) => w.identity));
  ids.delete(a.identity);
  const asks = (app.view.questions || []).filter((q) => ids.has(q.identity));
  const stuck = report?.stuck || [];
  const stuckOpen = !!app.stuckOpen?.has(a.identity);
  const line = (t) => h("li", {}, fileNodes(t));
  const draft = (text, steer = false) => () => draftCoordinator(a.identity, text, steer);
  return h("section", { class: "coordinator-report", id: "coordinator-report", "data-reading-key": "coordinator-report" },
    h("div", { class: "screen-head" }, h("h3", { class: "sub", text: "Team" }),
      aiTools(statusReportButton(a),
        h("button", { class: "btn btn-write", type: "button", on: { click: draft("Please focus the team on: ", true) } }, icon("chat", "btn-icon"), "Steer"),
        h("button", { class: "btn btn-write", type: "button", on: { click: draft("Please give a short team status: done, in progress, what is left, and any blockers that need me.") } },
          icon("chat", "btn-icon"), "Ask coordinator for status"))),
    teamBar(a),
    !row ? h("p", { class: "muted small", text: "Build a report from this team’s traces and output when you need it." }) : null,
    row && row.status !== "complete" ? h("p", { text: row.status === "failed" ? `Report failed: ${row.error}` : "Building report…" }) : null,
    row?.refresh_status ? h("p", { class: "muted small report-refresh", text: row.refresh_status === "failed" ? `${labels.refresh_failed}: ${row.refresh_error || ""}` : labels.refreshing }) : null,
    report ? h("p", { class: "muted small report-age" },
      report.status ? h("span", { class: `pill state s-${/stuck|blocked|fail/i.test(report.status) ? "caution" : "parked"}`, title: "Team status" },
        "Team status: ", fileNodes(report.status)) : null,
      ` ${labels.updated} ${clock(row.finished_at)} · ${row.out_of_date ? labels.out_of_date : "Cached report"}`) : null,
    block("needs", "Needs you", asks.map((q) => { const w = agentById(q.identity);
      return h("li", {}, h("a", { href: hashLink(sheet ? "graph" : "agent", q.identity) }, h("b", { text: w ? displayName(w) : "agent" }), ": ", mdLine(q.text, UI.ask_items.cut.line), " ›")); })),
    report ? [block("working", "In progress", (report.in_progress || []).map(line)),
      block("done", "Done", (report.done || []).map(line)),
      block("left", "What’s left", (report.left || []).map(line)),
      block("stuck", "Stuck", (stuckOpen ? stuck : stuck.slice(0, 3)).map((w) => h("li", {},
        h("a", { href: hashLink(sheet ? "graph" : "agent", w.identity), text: w.name }), ` · ${w.reason}`)),
        stuck.length ? h("div", { class: "actions tight" },
          stuck.length > 3 ? h("button", { class: "btn quiet", type: "button", text: stuckOpen ? "Show fewer" : `+${stuck.length - 3} more`,
            on: { click: () => { app.stuckOpen ||= new Set(); stuckOpen ? app.stuckOpen.delete(a.identity) : app.stuckOpen.add(a.identity); render(true); } } }) : null,
          h("button", { class: "btn btn-write", type: "button", on: { click: draft(`Please check on these workers: ${stuck.map((w) => `${w.name} (${w.reason})`).join("; ")}. What would help them continue?`) } },
            icon("chat", "btn-icon"), "Nudge coordinator about these")) : null, stuck.length)] : null,
    replySlot(`report:${a.identity}`, a),
    visualsStrip(`team:${a.identity}`, `identity=${encodeURIComponent(a.identity)}&team=1`));
}

function summaryBlocks(a, detail) {
  const row = detail?.summary, compact = a.summary;
  const status = row ? row.status : compact ? compact.status : "none";
  const s = row && row.status === "complete" ? row.summary : null;
  const stale = !!(detail?.presentation || a.presentation)?.out_of_date;
  const statusText = { complete: stale ? "Out of date" : "complete", queued: "queued", running: "summarizing…",
    failed: "failed", none: "none yet", skipped: "nothing to summarize" }[status] || status;
  const tick = (st) => st === "pass" || st === "passed" ? "s-waiting" : st === "fail" || st === "failed" ? "s-needs_you" : "s-unknown";
  const out = [];
  if (s) {
    if (s.headline) out.push(h("p", { class: "sum-headline" }, mdLine(s.headline, 0, true)));
    if (s.why_it_matters) out.push(block("why", contentLabel("why"), [h("li", { text: s.why_it_matters })]));
    const n = s.needs_you || {};
    if (!stale && a.question && n.kind && n.kind !== "none")
      out.push(block("needs", `${n.kind[0].toUpperCase()}${n.kind.slice(1)}`, [h("li", {}, n.ask || "",
        n.why ? h("p", { class: "muted small", text: `Why you: ${n.why}` }) : null)]));
    if (s.accomplished?.length) out.push(block("done", contentLabel("done"), s.accomplished.slice(0, 3).map((x) => h("li", {}, mdLine(x.replace(/(?:\/?[\w.-]+\/)+[\w.-]+/g, "the file"))))));
    if (s.tests?.length) out.push(block("tests", "Tests", s.tests.map((t) => h("li", {},
      h("details", { class: "test-row" }, h("summary", {}, h("i", { class: `dot state ${tick(t.status)}`, title: t.status }), ` ${t.what}: ${t.outcome}`),
        h("p", { class: "metric-meta", text: `${t.status} · "${t.evidence}"` }))))));
    if (s.claims?.length) out.push(block("claims", "Claims", s.claims.map((c) => h("li", {}, c.text, h("div", { class: "metric-meta", text: `${c.status} · "${c.evidence}"` })))));
    if (s.next_step && !stale) out.push(block("next", "Next", [h("li", { text: s.next_step })]));
    if (s.dropped?.length) out.push(h("p", { class: "note", text: `${s.dropped.length} statement(s) left out because the source does not contain them.` }));
  } else {
    if (status === "failed") out.push(h("p", { class: "error", text: `Summary failed: ${row ? row.error : compact.error}` }));
    const fb = detail?.fallback;
    if (fb?.available) {
      out.push(h("p", { class: "muted small", text: `Not an AI summary: the agent's last message (${clock(fb.at)}):` }));
      out.push(mdBlock(fb.text, { limit: UI.ask_items.cut.card, place: `fallback:${a.identity}`, cls: "fallback" }));
    } else out.push(h("p", { class: "muted small", text: detail ? `${a.label}. No message from the agent to show yet.` : "Loading…" }));
  }
  const cfg = app.view.summaries || {};
  return h("section", { class: "summary-blocks", "data-reading-key": "summary", "data-selectable": "summary", "data-agent-identity": a.identity },
    h("div", { class: "screen-head" },
      h("h3", { class: "sub" }, h("span", { class: "ai-tag", text: "AI summary" }), " ", h("span", { class: `sum-status ${status}`, text: statusText })),
      cfg.enabled === false ? null : aiTools(summarizeButton(a, { failed: status === "failed" }),
        h("button", { class: "btn btn-ai", type: "button", on: { click: () => requestSummary(a.identity, "longer") } }, icon("spark", "btn-icon"), "Summarize longer"),
        h("button", { class: "btn btn-ai", type: "button", on: { click: () => openAsk(a.identity) } }, icon("spark", "btn-icon"), "Ask summary agent"))),
    out,
    row?.status === "complete" ? h("details", { class: "summary-provenance fold", "data-reading-key": "summary-provenance" },
      h("summary", { text: "Summary details" }),
      h("p", { class: "metric-meta", text: `${row.resolved_model || row.model} · effort ${row.effort} · ${clock(row.finished_at)}${row.cached_from ? " · reused (same input)" : ""}` })) : null);
}

// ---------------------------------------------------------------- activity

const RIBBON_TONE = { working: "s-working", blocked: "s-needs_you", done: "s-done", idle: "s-waiting", unknown: "s-unknown" };

// The last 24 hours by state, from detail.activity. Time the collector did not watch is hatched;
// "≈" times get a soft edge. A tap on a segment jumps to its row.
function ribbon(a, activity) {
  const end = now(), start = end - RIBBON_HOURS * 3600;
  const items = [...activity].filter((i) => i.at).sort((x, y) => x.at - y.at);
  const segs = [];
  let first = items.find((i) => i.at >= start);
  const before = items.filter((i) => i.at < start).at(-1);
  let cursor = before ? start : first ? first.at : end;
  if (!before && cursor > start) segs.push({ from: start, to: cursor, tone: "unwatched", label: "not watched" });
  let current = before || null;
  for (const item of items.filter((i) => i.at >= start)) {
    if (current && item.at > cursor) segs.push(segment(current, cursor, item.at));
    cursor = Math.max(cursor, item.at);
    current = item.kind === "gone" ? null : item.status ? item : current;
    if (item.kind === "gone") segs.push({ from: item.at, to: end, tone: "unwatched", label: "gone", at: item.at });
  }
  if (current && cursor < end) segs.push(segment(current, cursor, a.present === false ? cursor : end));
  const merged = [];
  for (const seg of segs.filter((x) => x.to > x.from)) {
    const last = merged.at(-1);
    if (last && last.tone === seg.tone && last.approx === seg.approx) last.to = seg.to; else merged.push({ ...seg });
  }
  if (!merged.length) return null;
  const jump = (seg) => () => {
    if (!seg.at) return;
    (app.activityMore ||= new Set()).add(a.identity);
    app.activityFilter = { ...(app.activityFilter || {}), [a.identity]: "all" };
    render(true);
    requestAnimationFrame(() => {
      const row = document.querySelector(`.activity-list [data-at="${seg.at}"]`);
      if (row) { row.scrollIntoView({ block: "nearest" }); row.classList.add("is-flash"); setTimeout(() => row.classList.remove("is-flash"), 1200); }
    });
  };
  return h("div", { class: "ribbon-wrap", "data-reading-key": "ribbon" },
    h("div", { class: "ribbon", role: "img", "aria-label": `Last ${RIBBON_HOURS} hours by state` },
      merged.map((seg) => h("span", { class: `rib ${seg.tone}${seg.approx ? " approx" : ""}`, style: { flex: String(seg.to - seg.from) },
        title: `${seg.label}: ${clock(seg.from)}–${clock(seg.to)}${seg.approx ? " (≈)" : ""}`, on: { click: jump(seg) } }))),
    h("div", { class: "ribbon-scale muted small" }, h("span", { text: `${RIBBON_HOURS}h ago` }), h("span", { text: "now" })));
}

function segment(item, from, to) {
  const status = item.status || "unknown";
  return { from, to, tone: RIBBON_TONE[status] || "s-unknown", approx: !item.live, at: item.at,
    label: STATUS_WORDS[status] || status };
}

// One timeline: state changes, finished turns and alerts, newest first.
function activityRows(detail) {
  const events = detail?.events || [];
  const finishedAt = events.filter((e) => e.type === "completion").map((e) => e.at);
  const rows = (detail?.activity || [])
    .filter((i) => !(i.kind === "status" && i.status === "done" && finishedAt.some((at) => Math.abs(at - i.at) < 120)))
    .map((i) => ({ at: i.at, type: "state", live: i.live, li: timelineItem(i) }));
  for (const e of events) {
    if (e.type === "completion") rows.push({ at: e.at, type: "finished", live: !e.seen_late, li: h("li", { class: "s-done" },
      h("div", { class: "when", text: `${clock(e.at)}${e.state_label ? ` · ${e.state_label}` : ""}${e.seen_late ? " · seen late" : ""}` }),
      h("div", { class: "what" }, h("b", { text: "Finished a turn" }), e.title ? ` · ${e.title}` : "")) });
    else rows.push({ at: e.at, type: "alert", live: !e.seen_late, li: h("li", { class: "s-caution" },
      h("div", { class: "when", text: `${clock(e.at)}${e.resolved_at ? ` · over at ${clock(e.resolved_at)}` : " · open"}` }),
      h("div", { class: "what" }, h("b", { text: e.label || e.type }), e.title ? ` · ${e.title}` : "")) });
  }
  return rows.sort((x, y) => y.at - x.at);
}

function activityScreen(a, detail) {
  const filter = app.activityFilter?.[a.identity] || "all", more = !!app.activityMore?.has(a.identity);
  const all = activityRows(detail);
  const shown = all.filter((r) => filter === "all" || r.type === filter);
  const list = more ? shown : shown.slice(0, ACTIVITY_ROWS);
  const setFilter = (f) => () => { app.activityFilter = { ...(app.activityFilter || {}), [a.identity]: f }; render(true); };
  const answered = askItems(a).filter((i) => askAnswer(i) && askAnswer(i).delivery !== "failed").sort((x, y) => askAnswer(y).at - askAnswer(x).at);
  const s = asksOf(a);
  return h("div", { class: "activity-screen" },
    detail?.activity?.length ? ribbon(a, detail.activity) : null,
    h("div", { class: "segmented activity-filter", role: "group", "aria-label": "Show" },
      [["all", "All"], ["finished", "Finished"], ["alert", "Alerts"]].map(([f, label]) =>
        h("button", { type: "button", "aria-pressed": String(filter === f), on: { click: setFilter(f) }, text: label }))),
    list.length ? h("ul", { class: "timeline activity-list", "data-reading-key": `activity:${filter}` },
      list.map((r) => { r.li.setAttribute("data-at", String(r.at)); r.li.setAttribute("data-reading-key", `row:${r.type}:${r.at}`); return r.li; }))
      : h("p", { class: "muted small", text: detail ? "Nothing recorded yet." : "Loading…" }),
    shown.length > list.length ? h("button", { class: "btn quiet", type: "button", text: `Show more (${shown.length - list.length})`,
      on: { click: () => { (app.activityMore ||= new Set()).add(a.identity); render(true); } } }) : null,
    list.some((r) => !r.live) ? h("p", { class: "note", text: "≈ The collector noticed this later; the real time is unknown." }) : null,
    answered.length ? [h("h3", { class: "sub", text: "Your answers" }),
      h("ul", { class: "plain your-answers", "data-reading-key": "your-answers" }, answered.map((i) => h("li", { "data-reading-key": `answer:${i.id}` },
        h("details", { class: "answer-row" }, h("summary", {}, h("span", { class: "ask-tick", "aria-hidden": "true", text: "✓ " }), mdLine(i.text, UI.ask_items.cut.line)),
          h("p", { class: "muted small", text: repliedText(askAnswer(i)) }),
          i.quote ? h("blockquote", { class: "ask-quote" }, mdBlock(i.quote, { limit: UI.ask_items.cut.card, place: `answer-quote:${i.id}` })) : null)))),
      s ? askOriginal(a, s) : null] : null,
    h("h3", { class: "sub", id: "agent-conversation", text: "Messages" }),
    conversationCard(a),
    a.is_coordinator ? null : visualsStrip(`agent:${a.identity}`, `identity=${encodeURIComponent(a.identity)}`));
}

// ---------------------------------------------------------------- usage

function exactBadge(m) {
  return h("span", { class: `exact ${m.exact ? "yes" : "no"}`, text: m.exact ? "exact" : (m.kind || "estimate") });
}

function usageScreen(a) {
  const m = a.metrics || {}, t = m.trace || {}, c = m.context, tk = m.tokens, age = m.age, active = m.active;
  const parts = tk?.available ? [["input", "in"], ["cache_read", "cache read"], ["cache_write", "cache write"], ["output", "out"]]
    .map(([key, label]) => [key, label, tk.parts?.[key] || 0]) : [];
  const total = parts.reduce((n, p) => n + p[2], 0) || 1;
  const share = age?.available && active?.available && age.value > 0 ? Math.min(100, Math.round((active.value / age.value) * 100)) : null;
  const sources = [["Context", c], ["Tokens", tk], ["Age", age], ["Active", active]].filter(([, x]) => x)
    .map(([label, x]) => h("li", {}, h("b", { text: `${label}: ` }), [x.available ? x.source : x.reason || "no data", x.as_of ? `as of ${clock(x.as_of)}` : null, x.note].filter(Boolean).join(" · ")));
  const quiet = t.available && t.last_entry_at && a.last_activity_at && a.last_activity_at - t.last_entry_at > 6 * 3600;
  return h("div", { class: "usage-screen" },
    h("section", { class: "usage-part", "data-reading-key": "usage-runs" }, runsOn(a),
      a.launch?.sandbox ? h("p", { class: "small", text: `Sandbox: ${a.launch.sandbox}` }) : null),
    h("section", { class: "usage-part", "data-reading-key": "usage-context" },
      h("h3", { class: "sub" }, "Context ", c?.available ? exactBadge(c) : null),
      !c?.available ? h("p", { class: "muted", text: c?.reason || "unknown" })
        : c.window ? [h("div", { class: `meter lv-${contextLevel(c.percent)}`, role: "img", "aria-label": `${c.percent} percent of the context window` },
            h("i", { style: { width: `${Math.min(100, c.percent)}%` } })),
          h("p", {}, h("b", { text: `${c.percent}%` }), ` of ${tokens(c.window)}`, c.compactions ? ` · compacted ${c.compactions}×` : "")]
        : h("p", {}, c.value ? h("b", { text: `${tokens(c.value)} tokens` }) : "unknown", c.value ? " · window size unknown" : "", c.compactions ? ` · compacted ${c.compactions}×` : "")),
    h("section", { class: "usage-part", "data-reading-key": "usage-tokens" },
      h("h3", { class: "sub" }, "Tokens this session ", tk?.available ? exactBadge(tk) : null),
      tk?.available ? [h("p", { class: "big-number", text: tokens(tk.value) }),
        h("div", { class: "stack-bar", role: "img", "aria-label": parts.map((p) => `${p[1]} ${tokens(p[2])}`).join(", ") },
          parts.map(([key, label, n]) => n ? h("span", { class: `tk-${key}`, style: { flex: String(n / total) }, title: `${label} ${tokens(n)}` }) : null)),
        h("ul", { class: "stack-legend" }, parts.map(([key, label, n]) => h("li", {}, h("i", { class: `tk-${key}` }), `${label} `, h("b", { text: tokens(n) }))),
          tk.parts?.reasoning !== undefined ? h("li", { class: "muted" }, `reasoning ${tokens(tk.parts.reasoning)} (in out)`) : null)]
        : h("p", { class: "muted", text: tk?.reason || "unavailable" })),
    h("section", { class: "usage-part", "data-reading-key": "usage-time" },
      h("h3", { class: "sub", text: "Time" }),
      h("div", { class: "time-pair" },
        h("div", {}, h("span", { class: "stat-label", text: "Age" }), age?.available ? h("b", { text: `${age.exact ? "" : "≥ "}${hours(age.value)}` }) : unknown("unavailable"),
          age?.available ? h("span", { class: "muted small" }, ` since ${clock(age.since)} `, exactBadge(age)) : null),
        h("div", {}, h("span", { class: "stat-label", text: "Active" }), active?.available ? h("b", { text: `${active.exact ? "" : "≥ "}${hours(active.value)}` }) : unknown("unavailable"),
          active?.available ? h("span", { class: "muted small" }, " working ", exactBadge(active)) : null)),
      share !== null ? [h("div", { class: "meter thin", role: "img", "aria-label": `working ${share} percent of its life` }, h("i", { style: { width: `${share}%` } })),
        h("p", { class: "muted small", text: `working ${share}% of its life` })] : null),
    quiet ? h("p", { class: "note", text: `Trace quiet since ${clock(t.last_entry_at)}; numbers may be behind.` }) : null,
    h("details", { class: "fold", "data-reading-key": "usage-how" }, h("summary", { text: "How this is measured" }),
      h("ul", { class: "plain metric-meta" }, sources),
      h("p", { class: "note", text: t.available
        ? `Trace ${t.file || ""}${t.caught_up === false ? `, reading ${t.read_percent}%` : ""}; last entry ${clock(t.last_entry_at)}, read ${t.read_at ? ago(t.read_at) + " ago" : "?"}.${m.model ? ` Model: ${m.model}.` : ""} Tokens are not money; no prices shown.`
        : `No trace: ${t.reason || "unknown"}.` })));
}
