# Design: workspace-centred oversight (v0.2)

## The requirement

Your words after trying the phone page (7 Nov 2026): *"i want a nice UI/UX,
that also gives me info on the current state not just the notifications, but
the state of the workspaces, with their agents in there and how they divided
and whose working and quickly access what theyre working on or have been
working on etc."*

The brief that came with it:

- Workspaces at the top, each with status and counts (needs you / working /
  waiting / parked / done). Attention stays visible, but the overview has to
  be useful with zero alerts.
- Drill into a workspace: agents, current task, last activity, recent finished
  work, who is working vs waiting/parked/done, and the evidence.
- Quick access to recent output and history, on request, after login.
- Work division only from verified metadata. Never invent hierarchy or task
  assignments. Unavailable fields say "unknown".
- Freshness and uncertainty visible ("≥", "last seen").
- Mobile first, pleasant, with desktop parity; distinct controls look distinct.
- Choose workspaces on the phone, behind login and CSRF; nothing turns on by itself.
- Still read-only towards agents, no automatic AI calls.

## What the data can and cannot support

| Wanted | Source | Verdict |
|---|---|---|
| Workspaces, their names and order | `workspace.list` | verified |
| Which agents are in which workspace and tab | `agent.list` (`workspace_id`, `tab_id`) + `tab.list` (labels) | verified |
| Current task | agent's terminal title (`terminal_title_stripped`), set by the agent itself | verified, but it is the agent's own wording |
| Who is working / waiting / done | Herdr status (`working`, `idle`, `done`, `blocked`) | Herdr's screen-pattern guess, shown with evidence |
| Waiting vs parked | not in Herdr | a **time rule** on what the collector saw (see below), labelled as such |
| Last activity | collector's own observations: status or title changes it watched live, output fingerprint changes | verified by the collector; "no activity seen" when it has not seen any |
| What it has been working on | timeline of title and status changes, finished turns with the title at that moment | collector-recorded, kept 7 days |
| Supervisor / worker, who assigned what | **nothing in Herdr** | shown as "unknown". Tab names like "Coordinator" or "RL workers" are your labels; they are shown as tab names, never turned into roles |
| Peers | same tab | shown as "Same tab", with a note that it says nothing about who leads |

No new metadata source was added. `tab.list` is a read call on the same
socket; nothing is sent to agents.

## States

| State | Rule |
|---|---|
| Needs you | approval, question, unclear blocked screen, or the agent disappeared while busy |
| Working | Herdr says working. A possible stall is flagged inside it, not a separate state |
| Done, not reviewed | Herdr says done: finished a turn, nobody viewed the pane yet |
| Waiting | idle, and something happened within the last `ui.parked_after_minutes` (default 60) |
| Parked | idle, nothing seen for longer than that |
| Unclear | Herdr cannot classify the screen |

If the collector never saw an idle agent do anything, the quiet time counts
from when it was first seen and is shown with `≥`.

## Layout and interaction

**Phone (390 px and up)**

1. **Overview**: a "needs a look" card only when something does (red only for
   approvals, questions and failures; finished work is neutral), then one card
   per workspace with a state bar, counts and last activity. With zero alerts
   the cards are the whole page and still answer "what's going on".
2. **Workspace**: agents grouped by state in fixed order, each card with the
   task title, tab, last activity and last finished time. Below: "How it is
   arranged": tabs with their agents, plus the relationships-unknown note.
3. **Agent**: task, facts (state since, last activity, last seen, tab, folder,
   reports to: unknown, last finished), actions (show recent output, mark
   read, Explain disabled), why this state, finished work, recent alerts,
   timeline, same-tab chips.
4. **Workspaces and settings** (top-right button): the chooser with a switch
   per workspace (and mute), alert types, timing, phone-alert mode.
   With nothing watched, the overview *is* the chooser.

**Desktop (900 px and up)**: two-column workspace grid; workspace and agent
pages put the list next to the arrangement/timeline.

**Bar panel (Omarchy)**: same structure: needs-a-look rows, then workspace cards
that open in place to show agents by state and the tab arrangement; an
opened agent row shows facts, evidence and timeline (fetched with
`herdr-attention agent <id>`), recent output on request. Settings include the
chooser and the parked threshold.

Distinct controls: navigation is links/cards, settings are switches,
steppers and a segmented control, actions are pill buttons (primary only for
"Mark read"). Status is colour **and** text.

## Freshness

Top bar: "Live · updated 5s ago", "Herdr not reachable · last contact …", or
"Can't reach the laptop · last update …". "Last seen" says "here now" only while
Herdr is connected and the agent present. Timeline entries found later say
"noticed then, real time unknown".

## Privacy

- Unwatched workspaces never appear in the view, details, history or alerts.
  The chooser shows **only** their id and label, so they can be switched on.
  This is a deliberate change from v0.1 (where only the laptop could see
  them), made because you asked to choose workspaces on the phone.
- Output and timelines are fetched only when you open them, and only after login.
- Notifications are unchanged (agent, workspace, event type).

# Graph view and trace metrics (v0.3)

Requirement: requirements-followup-graph.md.

## One shared graph

- **One coordinate space.** Every watched workspace is a region (circle)
  in it; agents are nodes inside their region. Regions are laid out to match
  the screen's shape: stacked on a tall phone, in rows on a wide screen.
  Positions are deterministic (same data, same picture).
- **Edges, distinguishable in the legend:**
  - dashed spoke from the region's hub: *member of this workspace* (not a relationship)
  - solid arrow, parent → child: *spawned* (verified)
  - dotted arrow, original → fork: *forked* (verified)
  - hollow "?" node: the other end is hidden or unknown (unwatched workspace,
    or not in Herdr). No id or details are shown for it.
- **Verified means a trace says so.** Only Codex records it (`session_meta`:
  `parent_thread_id`, `forked_from_id`). Claude traces carry no cross-session
  link, so Claude agents get no relationship edges. Links may cross workspaces.
- **Interaction:** tap a node to select it (the URL becomes `#/graph/<id>`, so
  Back closes it); its links are highlighted and everything else is dimmed.
  One finger pans, two fingers pinch, the wheel zooms, and +/−/fit buttons do
  the same. Nothing needs hover. Keyboard: Tab to a node, Enter selects it.
  The list view stays the default; List/Graph switch at the top.
- **Node size:** context size, session tokens, or equal. A node without data
  for the chosen metric is small with a dashed outline.

## Metrics and where they come from

| Metric | Source | Exact? |
|---|---|---|
| Context | Claude: input + cache + output tokens of the latest main-thread request. Codex: `last_token_usage` plus `model_context_window` | estimate "as of the latest request" (compaction and resets show up automatically) |
| Tokens this session | Claude: each request counted once (dedupe by request/message id; retries and multi-block replies are not double counted). Codex: latest cumulative `total_token_usage` | exact once the trace is fully read |
| Age | session start in the trace; otherwise when the collector first saw it ("at least") | exact / at least |
| Active | time Herdr reported the agent as working, while the collector ran | observed / at least |

Each metric shows its source, its freshness ("as of …") and whether it is
exact. Missing data shows as "unavailable" with the reason. Input, cache read,
cache write, output and (Codex) reasoning tokens are shown separately. No
prices: tokens are not money.

## How traces are read

Passive and local. Files are found by the Herdr session id in
`traces.claude_dirs` (personal and work Claude folders) and
`traces.codex_dirs`, and only `<id>.jsonl` / `rollout-*-<id>.jsonl` inside
those folders are opened (no symlinks out, never auth or config files). Only
agents in watched workspaces are read. Reading is incremental from a saved
offset, at most 16 MB per file per pass, so a 70 MB trace catches up in a few
seconds without blocking anything. No text is kept, only numbers.

**Conversation excerpts** are read on request from the inspector ("last 10 /
25 / 50", with "Load older" paging): user and assistant text only, no
reasoning, no tool calls, injected system wrappers removed, cleaned and
redacted, 4000 characters per entry at most. They are shown apart from the
terminal lines and labelled as coming from the trace.

# Stall evidence (fix)

Requirement: requirements-followup-stall-fix.md.

Herdr 0.9.1 refuses to read the recent history of a busy full-screen agent
(`agent_not_idle`). The collector swallowed that error and then flagged a
"possible stall" on status alone. Now:

- Output checks read the **visible screen**, which works while an agent is
  busy and changes nothing. Timers, spinners and counters are removed before
  fingerprinting.
- A possible stall needs **at least 3 successful checks with the same content
  spanning the whole threshold**, the latest within the last 3 minutes. Status
  alone never raises one; with output checks switched off there are no stall
  flags at all.
- A failed check means "evidence unavailable": it is shown in the evidence, it
  resets the baseline, and it raises nothing. Status changes, collector
  restarts and Herdr disconnects also reset the baseline.
- When activity resumes or the evidence lapses, the stall flag is resolved
  automatically; questions and approvals are never touched by this.
- The output view uses the recent history when it can and falls back to the
  visible screen for busy agents, and says which one it shows.

# Graph first (v0.6)

Requirement: requirements-followup-graph-primary.md.

## The graph is the interface

Opening the page shows the graph, full screen, on phone and desktop. On top
of the canvas there are only light overlays:

- top left: **Herdr** and a status chip (agents, how many need you,
  connection state; bottom left on phones),
- top right, a tool palette: **To look at** (with the count), **Workspaces**
  (fly to a workspace, hide one from the graph), **View** (node size, filter
  by state, colour and line legend), **Settings** (watched workspaces, alerts,
  summaries, phone alerts, log out) and **List** (the text fallback),
- bottom right: zoom in, zoom out, fit.

Tapping an agent opens one contextual layer: a side panel on wide screens, a
bottom sheet on phones (a side panel again in phone landscape). It holds the
inspector: summary first, then why, metrics, connections and the
relationship editor, history, conversation and terminal. The palette panels
use the same layer. It never navigates away from the graph, and the selected
agent is panned (never zoomed) out from under it. Mark read closes it and
leaves the view exactly where it was. Escape closes it; `+`, `-` and `0`
zoom and fit.

The list views (`#/list`, workspace and agent pages) stay as the fallback and
the accessible text version.

## Herdr's status colours

Checked in Herdr's source for the installed version (0.9.1, tag v0.9.1,
commit `065ef9d6`), `src/client/shell.rs`:

| Herdr status | Palette token (`status_color`) | Symbol (`status_icon`, symbols style) |
|---|---|---|
| working | `yellow` | ◐ |
| blocked | `red` | × |
| done | `teal` (`blue` in Herdr's mobile view) | ✓ |
| idle | `green` | ○ |
| unknown | `overlay0` | · |

Your Herdr theme is `terminal` (`~/.config/herdr/config.toml`), which maps
those tokens to ANSI colours (`src/app/state.rs`): yellow = ANSI 3, red =
`LightRed` = ANSI 9, teal = `Cyan` = ANSI 6, green = ANSI 2, overlay0 =
`Gray` = ANSI 7. Your terminals take those from the Omarchy theme (now
Tokyo Night), so on 7 Nov the colours are: working `#e0af68` (amber, your
"orange"), blocked `#ff7a93`, done `#449dab` (teal-blue, your "bluish"),
idle `#9ece6a`, unknown `#a9b1d6`.

The collector re-reads this every minute (`herdr_attention/herdr_theme.py`):
Herdr's theme name, then the Omarchy palette (`ghostty.conf`, else
`colors.toml`) for `terminal`, or the hex values of Herdr's 17 built-in
themes, plus any `[theme.custom]` hex overrides. A theme switch is picked up
by itself. Herdr's theme is never changed.

In the dashboard every node shows the colour **and** Herdr's symbol. Our own
groupings map onto Herdr's statuses: needs you = blocked, working = working
(a possible stall gets a dashed outline), done = done, waiting = idle,
parked = idle with a dashed outline, unclear = unknown. "Agent gone while
busy" is not a Herdr status; it is red with a "!".

## Why it glitched, and the fixes

| Symptom | Cause (evidence) | Fix |
|---|---|---|
| Pinch zoom "shoots back" | When a finger lifted, the remaining finger kept panning from a baseline saved *before* the pinch, so the old scale came back. A scripted pinch on v0.5.0 went 0.64 → 1.91, then back to 0.64 when one finger lifted and the other moved; on a phone the remaining finger always moves a little | The gesture starts a new baseline from the **current** transform whenever the number of fingers changes; pinch keeps the world point under the fingers' midpoint |
| Nodes jumping around | Positions were recomputed every refresh; members were ordered by state, so any status change reshuffled them; sizes were relative to the largest value, so one agent's growth resized everyone and moved whole regions | Positions are cached per agent (relative to its region); only new agents are placed (in a free spot); order is by identity; sizes use a fixed log scale; regions only grow and move only if they would overlap |
| Flicker, lost inspector scroll | The whole SVG and page were rebuilt on every 10 s poll | One persistent SVG updated in place by id; the inspector is rebuilt only when what it shows changes, keeps its scroll and open sections, and never while you type |
| View jumping on resize / rotation | Re-fit on every resize | Fit happens once on first load (or when you press fit); after you touch the view it is never changed for you |
| Zoom fighting the browser, page scroll trapping | The graph sat inside a scrolling page | Full-screen canvas; `touch-action: none` only on the canvas; the page does not scroll in graph mode, the sheet scrolls inside itself |
| Stale data after quick taps | Overlapping requests could finish out of order | Refreshes and agent details ignore answers that arrive after a newer request |

Reduced motion turns off node transitions. There is no `<canvas>`: the
graph is SVG without a viewBox, one SVG unit = one CSS pixel, so
devicePixelRatio needs no handling.
