# Testing

```bash
python3 -m unittest discover -s tests -t .                 # ~115 tests, ~20 s, no Herdr needed
HERDR_LIVE=1 python3 -m unittest tests.test_herdr_live     # 9 read-only checks against the running Herdr
node tests/js/model.test.js                                # panel helpers
scripts/qml-check.sh                                       # QML duplicate scan + real Quickshell load
scripts/qml-preview.sh                                     # panel parts → docs/screenshots/panel.png
python3 scripts/web_screens.py                             # phone page at 390/1280 px → docs/screenshots/
```

The installer runs the unit tests and the QML check before installing.

## Which tests use real Herdr and which use fixtures

**Against the real, running Herdr** (`tests/test_herdr_live.py`, opt-in with `HERDR_LIVE=1`, read-only):
protocol version; agent fields; `state_change_seq` is one global counter;
`agent.get`/`explain`/`read`; per-pane status subscription starts; write
methods refused locally; `tab.list` covers every agent's tab; the collector's
snapshot and **workspace view** built from real data (counts add up, every
agent sits in a tab group, relationships "unknown", agent detail has a timeline);
**trace metrics** for the agents of one real workspace (at least one trace
found, every metric has a source or a reason, exact age, bounded conversation
read with user/assistant roles only).

**Recorded from the real Herdr, then replayed** (`tests/test_replay.py`):
26 real status events (anonymised) played through the collector.

**Fixture-based** (fake Herdr, fake clock, real engine/API/page code):

| File | Covers |
|---|---|
| `test_engine.py` | watched-only, one event per episode, restarts, identity, failures, stalls, ack |
| `test_workspaces.py` | **status** (needs you / working / done / waiting / parked), **grouping** by workspace and tab, **history** (timeline, last activity, finished-work titles), **state** detail, chooser privacy, v1→v2 database migration |
| `test_notify.py` | idle gating, summaries, rate limit, retries, metadata-only messages, ntfy request format |
| `test_api.py` | local socket and phone server: login, CSRF, Origin, rate limit, headers, unwatched never visible, chooser = labels only, phone workspace switching needs CSRF, agent detail only for watched agents |
| `test_web.py` | the shipped phone page in headless Chromium at **390 px and 1280 px**: overview, workspace, agent, settings, empty state; no horizontal overflow; tap targets ≥ 30 px; grouping order; unknowns and `≥` shown; **privacy** (hidden workspace only in the chooser, hidden agent never, hostile title stays text, output not fetched until asked, nothing POSTed on load) |
| `test_traces.py` | trace parsing: per-request dedupe (multi-block replies, retries), Codex cumulative snapshots, compaction, sidechains, incremental reads under a byte budget, replaced files, spawn/fork links, conversation paging and filtering (no reasoning/tool input/injected wrappers), redaction, only matching files inside the configured folders (symlink escape refused), metric provenance and "unavailable" |
| `test_graph.py` | observed active time (restart gaps not counted), cross-workspace links, hidden endpoints without ids, unwatched traces never read, `/api/conversation` login/bounds/paging/404, and the **graph page in Chromium at 390 and 1280 px** (one viewport, regions, edge types, selection highlights cross-workspace links, inspector contents, no conversation/terminal fetch until asked, no leaks) |
| `test_engine.Stalls` | **stall fix regressions**: failed busy read = unknown, status alone never flags, progress prevents the flag, timers/spinners are not progress, unchanged content over the threshold still flags, stale evidence/errors/disconnects/session turnover reset, recovery retires the flag without touching questions, busy agents read from the visible screen |
| `test_relations.py` | registry rules (self-link refused, reports_to never observed, labels declared only, several coordinators, handover, revocation, append-only history, cycles flagged, restart, identity rename), views (provenance, cross-workspace, hidden placeholders on either end without ids, gone endpoints, a new session in the same pane inherits nothing, trace + registered spawn merge), name/pane resolution, launch helper (refusals, registration, tokens only on the new pane, no prompts, no session id = no registration) |
| `test_graph.RelationshipsInTheGraph` | declared edges, role labels and cycle marks in the browser; inspector provenance, editor and history; **touch** (pointer events) and **keyboard** (focus + Enter) selection |
| `test_summaries.py` | summarizer with a stand-in CLI: isolated command and clean environment on the personal account, never the account wrapper, redaction/bounds/untrusted wrapping, watched only, one summary per item (never per refresh), content-hash reuse, coalescing, hourly limit, disabled, failure/retry/timeout/restart states, audit without money, option and claim verification, commit check against a real temporary git repo |
| `test_summary_ui.py` | browser at 390/1280 px: AI headline in lists or the plain label, summary card first and labelled with model/effort, exact options only, fallback and failed states, **mark read returns to the list keeping scroll**, **failure keeps the item open with the error**, graph inspector closes, terminal only on request |
| `test_graph_gestures.py` | **graph-first shell with scripted pointer/touch events**: pinch zoom stays after either finger lifts, through refreshes and panel open/close, world point stays under the fingers (also with a moving midpoint); tap selects, pan and second fingers never tap; wheel zoom anchored at the cursor; 12 refreshes during a pan with status/metric changes and an agent arriving and leaving: no zoom change, no ghost or duplicate nodes, unchanged agents never move; inspecting through refreshes keeps selection and viewport; newcomers do not overlap; mark read closes the inspector and keeps the viewport; Escape and zoom keys; layouts at 390×844, 844×390, 320×640, 1280×800 (sheet never covers the selected node or the zoom buttons, no page scroll); empty state and list fallback; Herdr colours and symbols present. The v0.5.0 pinch snap-back was reproduced with the same script before the fix |
| `test_sanitize.py`, `test_config.py` | cleaning/redaction, settings validation and persistence |
| `tests/js/model.test.js` | panel helpers: grouping, counts, facts with "unknown", timeline lines |

The browser and QML fixtures are generated by the real engine from fake
agents (`tests/web_fixture.py`), so their shape cannot drift from what the
collector sends.

**Desktop plugin**: every QML file is loaded and instantiated in the real
Quickshell 0.3.2 by `scripts/qml-check.sh` (Service, AgentRow expanded with a
timeline, WorkspaceCard opened, Panel), after a duplicate property/handler
scan. A visual check renders the parts offscreen (`qml-preview.sh`).

**Billed live test** (opt-in, `HERDR_SUMMARY_LIVE=1 python3 -m unittest tests.test_summary_live`):
one real Sonnet summary of a small sample through the exact production
command. Run once on 7 Nov 2026 (passed; see docs/summaries.md).

**Real data, read-only** (7 Nov, not a committed test): the live collector's
view (6 workspaces, 33 agents) rendered at phone and desktop width: pinch
zoom kept, 33 nodes, no node or region overlaps.

**Not covered by automated tests**: ntfy delivery to Android (ntfy is off);
the approval/question split on a real blocked agent (none was available).

## The QML check

1. `scripts/qml_dupes.py` scans each object block for a property, handler,
   signal or function set twice. It ignores JS object literals and function bodies.
2. Starts a throwaway `qs -p <tmpdir>` with Omarchy's `Commons`/`Ui` and no
   windows; creates each file with sample data, then quits. The plugin's CLI is
   pointed at `/bin/true`, so the test never reports presence.
3. Fails on load errors, duplicate-assignment errors or `TypeError`/
   `ReferenceError` lines from the plugin files (this caught a null `parent`
   in the workspace bar during v0.2).


v0.7 linking/routing fixtures: `tests.test_routing`, `tests.test_routing_linking`,
`tests.test_routing_ui`, `tests.test_layout`, and the local-only HTTP tests in `tests.test_api`.
The real, non-billed disposable-pane proof is opt-in:
`HERDR_LIVE=1 scripts/linking-live-proof.py` (see relationships.md for its explicit
idle-session bootstrap and native-hook coverage limit).


Step 3b fixtures: `tests.test_layout_watch` reproduces the seven-pane codex tab,
five unnamed panes, numeric tab 1 and generic titles; it verifies watch privacy,
quiet attention state, exact suggested commands, naming/dedupe/no-focus, and
report plus API-confirmed merge evidence. `tests.test_layout_ui` renders the
workspace/tab and graph-workspace hints in real Chromium. GitHub state is mocked
in tests; no test uses live credentials or a model. Live `layout check`/`doctor`
are read-only and never execute suggested repair commands.


v0.7.1: `tests.test_session_collisions` covers simultaneous duplicate native ids,
independent per-pane status/history/labels, incremental discovery, reversed
snapshots, collector restart, pane moves and disappearance of the other claim.
`tests.test_empty_workspaces` uses real Chromium at phone/desktop widths to check
one collapsed empty-workspace group, stable expansion across refresh, pruning of
gone nodes/regions/endpoints, and continued historical detail access. Panel model
helpers and a real Quickshell load cover the desktop grouping. Live verification
compares present pane ids against Herdr read-only; it never prompts/restarts w4.

v0.7.2: `tests.test_inference` covers nested first-prompt briefs, exact recent
coordinator references, common-brief siblings, ambiguous/missing/old/colliding
evidence, watch privacy, read bounds and path escapes, declared precedence,
promotion, persistent worker-wide unlink vetoes, cycle rejection and dry
ignore-declaration checks with no database writes. It renders selected inferred
and declared links in Chromium at 390/1280px and checks computed dashes, opacity,
evidence tooltips and overflow. `tests.test_api` keeps dry overrides local-only.
The JS model check and real Quickshell harness exercise both dashed/fainter and
solid desktop relationship components. Launch fixtures verify the global-rules
pointer for Claude, Codex, Cursor and agy, with and without a brief.

v0.7.3: `tests.test_quotas` covers the read-only Omarchy usage-record
mirror (multi-account Claude, stale/expired windows, missing dir, secret
redaction). `tests.test_web` checks the overview quota strip on phone and
desktop, including the empty-workspace page.

For the live proof, use `herdr-attention inference check --ignore-declared
w4:p4D --ignore-declared w4:p4E`; compare declarations before and after.
This does not delete declarations, persist temporary inference or contact agents.
