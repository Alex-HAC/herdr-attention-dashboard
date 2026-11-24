# Requirements: agreed, proposed, open

Source: `Hardware/Herdr — attention dashboard plan.md` in the vault (7 Nov
2026). This page keeps the three kinds apart and says where each lives.

## Second iteration (7 Nov 2026): workspace-centred oversight

Asked for after using the phone page: a workspace-centred view of what is
going on, not a notification feed. Full requirement, data audit and design
rationale: [design.md](design.md).

| Requirement | Status |
|---|---|
| Workspace overview with state counts; useful with zero alerts | Done (phone, desktop browser, bar panel) |
| Drill-down: agents, task, last activity, finished work, evidence | Done |
| Recent output and history on demand, after login | Done (`/api/output`, `/api/agent`) |
| Work division from verified metadata only; "unknown" otherwise | Done: tabs from `tab.list`; relationships always "unknown" |
| Freshness and uncertainty shown | Done (`≥`, "last seen", "noticed then, real time unknown") |
| Mobile first, desktop parity | Done; browser tests at 390 px and 1280 px |
| Choose workspaces on the phone, login + CSRF, nothing automatic | Done. **Changes an earlier rule**: the phone may now see the *labels* of unwatched workspaces in the chooser (nothing else about them) |
| Read-only towards agents, no automatic AI | Unchanged |

## Fifth iteration (7 Nov 2026): graph first, Herdr colours, no glitching

| Requirement | Status |
|---|---|
| Graph is the default and the whole interface; list as fallback | Done ([design.md](design.md#graph-first-v06)) |
| Everything reachable from the graph (workspaces, attention, inspector, metrics, legend, settings, logout) | Done (palette + one contextual sheet/panel) |
| Herdr's real status colours, verified from source and recorded | Done: `status_color` in Herdr 0.9.1 source; resolved from the current theme at runtime |
| Fix the real causes of the glitching, pinch snap-back first | Done; cause shown with a scripted reproduction on v0.5.0 |
| Scripted touch/pointer tests across refreshes and drawer open/close, widths 390/844/320/1280 | Done (`tests/test_graph_gestures.py`) |

## Third iteration (7 Nov 2026): graph view, trace metrics, stall fix

| Requirement | Status |
|---|---|
| One shared graph, workspaces as regions, links may cross | Done ([design.md](design.md#graph-view-and-trace-metrics-v03)) |
| Verified relationships only; distinct edge types and legend; hidden/unknown endpoints | Done. Codex spawn/fork from traces; Claude has none |
| Tap to inspect, stable inspector, touch pan/zoom, no hover | Done; browser tests at 390/1280 px |
| Node size: context vs cumulative tokens (input/output/cache separate) | Done |
| Age (trace start, else first seen) and active time (observed working) with units | Done |
| Provenance, exact/estimate and freshness per metric; "unavailable" otherwise; no money | Done |
| Passive, incremental, bounded trace parsing, watched only, dedupe | Done (`traces.py`) |
| Inspector: status, task, evidence, metrics, connections, history, last-N conversation with paging, separate terminal | Done |
| Stall fix: busy-safe reads, evidence over time, errors = unknown, flags retire | Done ([design.md](design.md#stall-evidence-fix)) |
| Explicit coordinator/reporting tracking | Done ([relationships.md](relationships.md)): registry with provenance, CLI + UI, launch helper, metadata tested on a throwaway pane |
| Summary-first cards, summarizer, readable output, mark-read closes | Done ([summaries.md](summaries.md)); one billed live Sonnet run verified |

## Agreed (built as specified)

| Requirement | Where | Status |
|---|---|---|
| Desktop plugin + local collector first, then phone page + ntfy over Tailscale | `herdr_attention/`, `plugin/`, `herdr_attention/web/` | Desktop done and installed. Phone page and ntfy sender built and tested, **off** until setup is approved ([phone.md](phone.md)) |
| Only watch workspaces you enable; new ones start off | `watch.enabled_workspaces` (empty by default); engine drops everything else; collector only subscribes to watched panes | Done. Tested: unwatched workspaces never appear in API, dashboard, history or alerts |
| Read-only in v1, room for steering later | Herdr client refuses write methods; API has no input routes; `features.steering: false` in the view | Done. Tested |
| All event types notify at first; toggle per type; adjustable stall time; per-workspace enable/mute | `events.*`, `stall.minutes`, `stall.per_workspace`, `watch.muted_workspaces` | Done, in the panel's Settings, the CLI and the phone page |
| Status + recent output by default; AI Explain only on tap; no model polling | Output read on request only (cleaned, redacted). Explain button present but disabled: backend not chosen | Done. No model calls anywhere |
| Notifications carry only agent, workspace, event type | `notify.py` `_compose` | Done. Tested that titles, paths, rule ids and pane ids never appear |
| Phone alerts only after 5 min without input; adjustable; manual override | `presence.phone_delay_minutes`, `presence.mode` (auto / always / never) | Done. Tested |
| Idle is a guess, never keystrokes | Plugin sends only idle/active + idle seconds | Done |
| Held-back alerts still show on the desktop | Engine records everything; notifier only decides sending | Done. Tested |
| No false promises: no invented timestamps, stalls are guesses | Collector clock only; `since_known` / `≥`; evidence + confidence on every flag | Done |

## Proposed in the plan, not yet agreed (built, switchable)

| Proposal | What was built | Switch |
|---|---|---|
| Notify once per event, then quiet until the state changes | One event per state episode; each event sent at most once. (The acceptance tests require this too.) | always on |
| Read/acknowledged state per event | *Mark read* in panel and phone page | `ui.acknowledge` |
| Needs-you first, then working, then the rest | Default sort and panel sections | `ui.group_attention_first` |

## Open decisions (defaults used, change any time)

| Decision | Default used | Note |
|---|---|---|
| Stall threshold | 15 min, per workspace possible | as proposed |
| Held-back alerts | While active: nothing to the phone. When you go away: one message with everything still open and unread, then live alerts | as proposed |
| Retention | 7 days of resolved/read events | as proposed; ntfy cache untouched |
| Explain backend | none | phase 4 |
| Idle interface | Quickshell `IdleMonitor` inside the plugin (Wayland idle-notify) | verified in phase 0; no hypridle needed |
| Names | project `herdr-attention-dashboard`, command `herdr-attention`, plugin `alexvdb.herdr-attention`, keyring service `herdr-attention-dashboard`, unit `herdr-attention.service` | rename before first backup if you prefer others |
| Herdr agent integrations | left as they are (codex current, claude outdated) | updating touches shared Claude hooks: your call |
| Phone access | dashboard passphrase in the keyring; ntfy user + token | see [phone.md](phone.md) |
| Waiting vs parked | idle and quiet for more than 60 min = parked | `ui.parked_after_minutes`, changeable on phone and laptop |
| Unknown presence (plugin not reporting) | counts as **away** | `presence.unknown_means`; chosen so you are not silently missed if the shell is down |
| Failure detection | agent disappears while working/blocked | Herdr gives no exit codes; closing a busy pane on purpose also counts |

## Acceptance tests from the plan

| Test | How it is covered |
|---|---|
| Unwatched workspaces never show up | `test_engine.Watching`, `test_api` (both servers), `test_replay` |
| Status changes → right events; no duplicates after reconnect/snapshot | `test_engine.StatusEvents`, `test_replay` (real recording) |
| Restart / session change keeps identity and history | `test_engine.Identity`, `test_engine.Restarts` |
| Settings survive restarts | `test_config` |
| Idle/paused not shown as blocked; blocked shows evidence | `test_engine.StatusEvents` |
| Idle gating: delay, input cancels, override, dashboard records all | `test_notify.Gating` |
| Each event alerts once; offline catch-up bounded and marked | `test_notify` (summary limit, "seen late"), `test_engine.Restarts` |
| Notification = agent, workspace, event + link, no output | `test_notify.Content` |
| Output escaped, control codes stripped, redaction | `test_sanitize`; QML uses PlainText, phone page uses textContent |
| API needs login + CSRF protection | `test_api.PhoneApi` |
| Laptop/phone offline behave sensibly | Phone page shows "last seen, not proof of sleep"; panel shows collector/Herdr offline. Manual |
| Android delivers with screen off; battery | **Not done**: needs the ntfy setup and your phone |
| QML real-engine load + duplicate scan | `scripts/qml-check.sh` (run by the installer) |
| AI only on Explain tap | No AI code path exists; Explain is disabled |
