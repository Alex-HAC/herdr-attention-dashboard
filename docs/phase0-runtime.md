# Phase 0: what the installed Herdr actually does

Checked on 7 Nov 2026 against Herdr 0.9.1, protocol 22, from inside a Herdr
pane, read-only. `tests/test_herdr_live.py` re-checks the important points
(`HERDR_LIVE=1`).

## Socket protocol

- Socket: `~/.config/herdr/herdr.sock` (or `$HERDR_SOCKET_PATH`), mode 0600.
- **One request per connection.** Send one JSON line
  `{"id": "...", "method": "...", "params": {...}}`, read one JSON line back.
  A second request on the same connection gets a broken pipe.
- Replies are `{"id", "result"}` or `{"id", "error": {"code", "message"}}`.
- Results are wrapped: `agent.list` → `result.agents`, `agent.get` →
  `result.agent`, `agent.read` → `result.read.text`, `agent.explain` →
  `result.explain`. The CLI's `--json` output is *unwrapped*, which is easy to
  miss (the live test caught this for `explain`).

## Events

- `events.subscribe` with `{"subscriptions": [...]}` keeps the connection
  open. First line: `{"result": {"type": "subscription_started"}}`, then one
  envelope per line: `{"event": "pane.agent_status_changed", "data": {...}}`.
- **`pane.agent_status_changed` needs a `pane_id`.** There is no "all panes"
  subscription; without `pane_id` Herdr answers `missing field pane_id`. The
  collector subscribes per agent pane and re-subscribes when panes appear.
- No initial event on subscribe: you must take a snapshot yourself. The
  collector subscribes first, then snapshots, so nothing falls in between.
- Status event payload is minimal: `agent`, `agent_status`, `pane_id`,
  `workspace_id`. No timestamp, no sequence number, no title in practice.
  The collector calls `agent.get` after each event for the rest.
- Subscribable lifecycle events used: `pane.created/closed/exited/updated/moved`,
  `pane.agent_detected` (with `released` and `final_status`),
  `workspace.created/closed/renamed`.
- `pane_output_changed` exists in the event schema but is **not
  subscribable** (not in the subscription list). `pane.output_matched` needs
  a pattern and was not used.
- In two recordings (5 + 3 minutes, 8–26 status changes) no `pane.updated`
  events arrived, so title changes do not flood the stream.

## Fields

- `state_change_seq`: one **global** counter across all panes; it rose
  2084 → 2088 for one pane while other panes took 2085–2087. Every status
  change takes a new number. Used to tell "nothing changed while we were
  away" from "it changed and came back".
- `revision` (agent info): small per-pane number (2–9) that did **not**
  change across status changes. Meaning unknown; not used.
- `revision` in `agent.read` results: always 0. Not an output signal.
- `agent_session.value`: present for Claude and Codex agents
  (`source: herdr:claude` / `herdr:codex`). Used as the agent's identity.
- `name` is **missing entirely** (not null) for agents nobody named.
- Workspace ids (`w1`, `w7`, ...) are stored in Herdr's `session.json` and
  survive server restarts; labels can be renamed. Settings key on the id.

## Status meaning

- `done` = the agent finished a turn and nobody has viewed the pane yet.
  Focusing the pane turns it into `idle`. **Reads do not mark it seen**, so
  the collector can read output without changing anything.
- Short `working → idle` flaps (3 s) happen and are not completions.
- `blocked` covers both approval dialogs and questions. `agent.explain` gives
  the matched rule id, which tells them apart:
  - Claude: `bash_permission_prompt`, `generic_permission_prompt` (approval);
    `live_blocked_form`, `live_blocked_form_named_session`,
    `dynamic_workflow_prompt`, `mcp_elicitation_prompt` (question);
    `legacy_no_prompt_blocker` (unclear).
  - Codex: `trust_directory`, `startup_update` (approval-like),
    `live_strong_blocker`, `weak_blocker`, `osc_title_blocked` (unclear or question).
  The mapping is config (`classify.*_rules`). Only the rule id is kept; the
  explain output also contains screen text, which is dropped.
- No live `blocked` agent was available while building (v1 cannot create one
  without steering), so the approval/question split is verified against the
  rule list, not against a real blocked agent.

## Output change

There is no verified output-change event. The collector measures it itself:
for working agents in watched workspaces it reads the last 40 lines every
60 s, removes digits and spinner characters (timers and token counters
change every second), and keeps only a hash. The text is never stored.

## Busy agents and output reads (found 7 Nov, 14:47)

`agent.read` with `source: recent` fails on a working full-screen agent
(Codex): `agent_not_idle: cannot read N lines while <pane> is working: its
alternate-screen history can only be captured by scrolling while idle … or
use --source visible`. `source: visible` works on busy and idle agents and
returns the screen as shown. The collector now uses `visible` for stall
checks and falls back to it for the output view.

## Session traces

- Herdr's `agent_session.value` is the agent's own session id: Claude
  `<project dir>/<id>.jsonl` (in `~/.claude-personal/projects` or
  `~/.claude/projects`), Codex `~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl`.
- Claude: several lines per reply (one per content block, same `message.id`
  and `requestId`); `usage` has input, cache creation, cache read and output;
  `system`/`compact_boundary` marks a compaction; `isSidechain` marks
  subagent turns. No field links one session to another.
- Codex: first line `session_meta` (start time; `source.subagent.thread_spawn.parent_thread_id`,
  `parent_thread_id`, `forked_from_id` for spawned or forked threads);
  `event_msg`/`token_count` carries cumulative `total_token_usage`,
  `last_token_usage` and `model_context_window`; `turn_context` has the model.

## Integrations

`herdr integration list`: `codex` is `current`, `claude` is `outdated`, the
others are not installed. These were left untouched. Updating the Claude
integration would change the shared Claude hooks, which is out of scope and
needs your OK.

## Idle signal

- Omarchy's idle service (`omarchy-shell idle status`) only reports idle
  after the screensaver timeout and switches off with "stay awake", so it does
  not fit the 5-minute rule.
- Quickshell 0.3.2 has `IdleMonitor` (Wayland ext-idle-notify). The desktop
  plugin runs its own with a 30 s timeout and `respectInhibitors: false`
  (only real input counts), and reports idle/active plus a heartbeat every
  60 s. hypridle is not installed and is not needed.

## Omarchy plugin host

- User plugins live in `~/.config/omarchy/plugins/<id>/` with a
  `manifest.json`; kinds `bar-widget` + `service` are both supported.
  `omarchy plugin enable/disable <id>` toggle them through shell IPC.
- Bar widgets exist once per monitor; the service exists once. The panel
  finds its service with `bar.shell.serviceFor(id)`.
- Panel file changes hot-reload. A running service instance is kept across
  rescans, so **changes to `Service.qml` need `omarchy restart shell`**.
- Every user plugin logs "Module path ... is outside of the config folder";
  that warning is harmless.
