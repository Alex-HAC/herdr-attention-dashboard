# Coordinators, reporting lines, teams

Requirement: requirements-followup-relationships.md.

Herdr does not know who leads whom. This tool keeps that information itself,
in an append-only registry in its own database, keyed by each agent's session
id (not its pane). Nothing here contacts an agent: declaring a reporting line is
configuration, like choosing which workspaces to watch.

## The three kinds of facts

| Fact | Means | Comes from |
|---|---|---|
| **spawned by** | which agent started which. Creation, not supervision | a Codex trace (`observed-spawn`), or the launch helper knew the caller (`registered`) |
| **reports to** | who an agent reports to | launch helper (`registered`), you (`manual`), or specific evidence as an `inferred` fallback |
| **role / team / task** | labels such as `coordinator`, `worker`, a team name, a task | only declarations |

Rules: being started by an agent does not make it your coordinator; sharing a
workspace or tab is not a relationship; nothing is ever inferred from names or
titles. Roles stay declared-only; reporting lines without a declaration or
specific evidence remain unknown.

Policies: an agent cannot be linked to itself (refused). Reporting cycles are
allowed for declarations but every edge in the loop is marked as a cycle, so
you can fix it. Inferred links are never allowed to close a cycle.
An agent may report to several coordinators. A team and a task are single
values (setting a new one replaces the old one); roles add up.

## Declaring things

From any terminal (the collector must be running):

```bash
herdr-attention team role ctrl-lead coordinator          # add a role
herdr-attention team role ctrl-lead coordinator --clear  # revoke it
herdr-attention team team split-s release                # set the team (replaces)
herdr-attention team task split-s "S4 core additions"
herdr-attention team link split-s --reports-to ctrl-lead  # add a reporting line
herdr-attention team unlink split-s --reports-to ctrl-lead
herdr-attention team assign split-s --to split-lead       # hand over: replaces all current coordinators
herdr-attention team list                                 # declarations and effective inferred links
herdr-attention team history <identity>                   # audit trail for one agent
```

Agents can be named by their Herdr name, their pane id or their identity;
only agents in watched workspaces can be named. On the phone or in a
browser, open an agent in the graph → **Declare role, team or reporting line**
(login and CSRF protected; recorded as `manual` by `ui`; every row has a
remove button; the history is listed below it).

## The launch helper (v0.7.0)

```bash
herdr-spawn worker-1 --role backend --reports-to me --team release --brief /path/brief.md -- "Implement the brief"
herdr-attention team start worker-1 --role tests --dry-run
herdr-spawn idle-check --role tests   # global-rules pointer still sent; session registration may wait
```

`herdr-spawn` is the short alias for `herdr-attention team start`. Run it from
inside a Herdr pane. Routing chooses model, effort and a project-allowed account;
`--agent NAME`, `--kind PROVIDER`, `--model`, and `--effort` override defaults.
The layout policy groups workers into provider/role tabs, caps tabs at four panes,
and gives coordinators their own named tab. `--tab` forces a fresh group tab;
`--split` requests a sibling pane, overflowing to a new tab if the current one
is full. `--direction right|down` and `--cwd DIR` control placement. `--name`
overrides the positional name; omitted names come from role/provider/task. Invalid
names are repaired to Herdr's slug syntax and collisions get `-2`, `-3`, capped
at 32 characters. Dry runs show the chosen name, title, tab and placement. Name,
terminal title and role/team/task tags are set on the newly created pane. Topology
commands use `--no-focus`; `--focus` is an explicit opt-in. The native `pane run`
launch preserves focus without a separate focus command. Accounts never switch globally.

Since v0.7.2 every provider's first prompt starts with
`Global rules: read ~/.config/agents/AGENTS.md`, then the optional brief pointer
and task. The wrapper never edits that source, its symlinks or the supplied brief.

## Passive reporting fallback (v0.7.2)

The trace worker evaluates watched, present, unambiguous sessions on discovery
and its normal trace poll (30 seconds by default). It uses the first genuine
user prompt naming a Markdown file in a `briefs` directory, following at most
six explicit references inside that directory. Each brief is capped at 64 KiB;
symlinks and `..` cannot escape the named brief directory. Each session read is
bounded to a 256 KiB head plus a 512 KiB tail, cached until the trace changes.

Specific supported evidence, with heuristic confidence (not a probability):

- A referenced brief explicitly says `Coordinator: ... w4:p3B` (0.97).
- A declared coordinator's trace references the worker's exact brief within
  30 minutes before to 5 minutes after worker start (0.90).
- A declared sibling started within 12 hours and shares the same explicitly
  referenced common brief in the same brief directory (0.85).

The coordinator must be a watched live agent, and its trace must establish that
its session started before the worker. An explicit `Coordinator:` brief reference
can identify an otherwise unlabelled coordinator without assigning it a role.
The other evidence methods require an existing coordinator declaration or a
declared reporting link to that coordinator. Contradictory evidence is rejected.
Shared native sessions, names, tabs, bare directory matches and unknown start
times do not qualify. No pane command, prompt, interrupt or model call occurs.
Standalone pane-command inference without a named first-prompt brief is not
currently supported; such agents need `team link`.

Effective fallback links and their evidence/confidence are stored in the app
database separately from the authoritative append-only declaration registry.
Declared `reports_to` always wins, even if its coordinator is hidden or gone.
Inference adds at most one coordinator and never closes a cycle. Inferred
links require both endpoints to remain watched, present and unambiguous.
They appear dashed and fainter in the phone graph and desktop relationship
list. Details show `inferred: <evidence>` and confidence; team list shows
`provenance: ["inferred"]`, `inferred: true` and `dashed: true`.

`team link WORKER --reports-to COORDINATOR` promotes an inferred link to a
solid declaration. `team unlink` removes a matching line and permanently
suppresses inference for that worker's identity, across restarts and changing
evidence. Unlinking a declaration also suppresses automatic fallback. An
explicit declaration can still be added after suppression. `spawned_by` unlink
does not suppress reporting inference. A new native session is a new worker.

For a dry, local-only evaluation without changing declarations:

```bash
herdr-attention inference check --ignore-declared w4:p4D --ignore-declared w4:p4E
```

Ignored declarations are excluded from evidence as well as precedence, only in
memory. Permanent suppression still applies. `[inference] enabled = false`
disables the fallback; disabling traces also disables all inference reads and
rendering. No transcript excerpts are persisted as relationship evidence.

The launch command runs `env … AGENT` through `herdr pane run`: only the child
agent gets `HERDR_PARENT_PANE`, `HERDR_PARENT_SESSION`, a fresh `HAT_RUN_ID`,
and the selected account environment. The pane shell never receives them, so
an unrelated agent later started in that shell cannot inherit the old parent.
This uses `pane run` because Herdr 0.9.1 `agent start` has no child-env parameter.
No prompt is sent through Herdr; the first prompt is a native CLI argument.

Full permissions are the default from `[permissions.yolo]`. Opt out with
`--sandbox "reason"`; the reason appears on the card and in the registry.
Native flags after `--` are refused: put model/effort/sandbox choices on the wrapper.
A dry run shows the command, environment **names**, tags and routing explanation;
environment values are redacted, and the child-session hash is a placeholder.

The collector registers the run before launch and binds it to the detected
session. `spawned_by` has provenance `registered`, actor = caller session.
`reports_to` is registered only when `--reports-to` is present. The wrapper
writes `parent=<caller pane>`, `self=<16 hex SHA-256 of child identity>`, and
`hat_parent_self=<hash of caller identity>` on its new pane. The collector
ignores tags unless **both** current sessions match. Parent reuse, child reuse,
ambiguous panes, self-links and cycles cannot fabricate a tag relationship.
Persisted links belong to the original session, never its replacement.

The hash-validation approach is inspired by
[Algorant/herdr-agent-tree](https://github.com/Algorant/herdr-agent-tree), MIT.
Our tokens and collector registry are independent of that plugin's tokens.

## Per-launch hooks and sidebar

Only wrapper launches receive the extra hooks: Claude `--settings FILE`,
Codex per-invocation `-c hooks.EVENT=…` overrides. These cover SessionStart,
SessionEnd, SubagentStart, SubagentStop and Stop; Claude also has TaskCompleted.
The hook sends identifiers and event type to **only the local Unix socket**.
It drops prompts, transcripts and assistant messages; failures cannot block or
continue the agent. A main-session SessionStart/Stop also publishes that launch's
session id through Herdr's provider session-metadata interface, after checking
that the pane still holds the matching provider/session. No shared Claude settings/hooks/skills are installed.

Hook subagents appear as in-process graph nodes under their observed parent
(`observed-spawn`); they never gain a reporting line. Claude task ids are scoped
to the parent session. Codex uses the child thread id. Cursor/agy launches get
registration/tags; no unverified lifecycle hook interface is enabled for them.
Native hook invocation is parser-checked for Codex; Claude lifecycle and nested
subagent cases are fixture-tested, not billed live model tests.

The native `herdr-plugin/herdr-plugin.toml` is linked by the installer. Its
startup checks the installed command; its actions control the collector's projection; it keeps no relationship
store. The collector's Herdr projection uses `agent.view.set` with source
`plugin:herdr-attention`: session-checked display metadata, preorder sorting,
indentation and role badges. It has no start/prompt/input/focus API capability.
Another plugin's view owner makes it passive. `[sidebar] enabled = false` in
config.toml disables it; temporary display tokens expire within ten seconds.
The DB remains the sole durable record. In-process subagents have no Herdr pane,
so they appear in the dashboard graph, not as separate native Herdr rows.

## What Herdr's metadata tokens do (tested 7 Nov 2026 on a throwaway shell pane)

| Question | Result |
|---|---|
| Write and read back | tokens show in `pane get`, `pane list`, `api snapshot` and `agent get` |
| Events | every write fires `pane.updated`, and the event includes the tokens |
| TTL | `--ttl-ms 2000` disappeared after 2 s; without a TTL they stayed for the whole test (long-term and across Herdr restarts: **unverified**, Herdr was not restarted) |
| Two sources, same token name | last writer wins; there is one flat map per pane, not one per source |
| Clearing | any source can clear any token |
| `--seq` | a lower seq from the same source is ignored silently (exit 0, value unchanged) |
| Names | invalid names are rejected (`invalid_metadata_token`) |
| Agent leaves / new session in the pane | **tokens stay on the pane**, so a new agent would inherit them |

That last row is why tokens are only a hint and the registry is the source
of truth: roles and lines belong to a session, so a new session in the same
pane starts with none.

## Edge cases

- **Session replaced** (new agent in the same pane): the old declarations
  stay with the old session, which shows as "gone"; the new one has none.
  `claude --resume` keeps the same session id, so it keeps its declarations.
- **Agent closed**: history is kept; links to it show a "gone: <name>"
  endpoint while its workspace is watched.
- **Restarts**: the registry is in the collector's database and survives
  collector restarts. Herdr restarts were simulated in tests only.
- **Unwatched workspaces**: an endpoint there is shown only as "hidden or
  unknown agent", without id, name or details, in the graph, the inspector
  and the history.
- **Several coordinators, handover, revocation**: all supported and recorded.


## Non-billed live proof

After installation, from a Herdr pane on the work Codex account:

```bash
HERDR_LIVE=1 scripts/linking-live-proof.py
```

Codex 0.169 creates sessions lazily. This opt-in test creates a fresh local test
thread via `thread/start` + `thread/inject_items` (never `turn/start`), resumes it
through the launch engine, and bootstraps **only that idle test pane's session
metadata**. It does not manually declare relationships. It checks actual collector
registration, matching tags, stale-tag rejection, restoration, native sidebar
activation/indentation and closes its disposable pane. The temporary test thread
is retained as non-model evidence. Native lifecycle-hook invocation with a real
model turn remains untested; hook normalization/ingest are fixture-tested.


## Quiet layout diagnostics

```bash
herdr-attention layout check
herdr-attention layout check --workspace w4
herdr-attention layout check --workspace w4 --json
herdr-spawn --role backend --task catalog --name "Catalog Worker" --dry-run
```

The same findings appear in workspace/tab views, the panel, and `doctor`:
unnamed agents, tabs above the configured pane cap (shell panes count), numeric
or default tab labels, and empty/generic titles such as “Continue the task”.
They are quiet layout hints: they create no attention event, alert, or badge.
The check prints exact suggested rename/title commands; it executes none.
Only watched workspaces appear, including when filtering by `--workspace`.

Finished idle/done panes get a “consider closing after review” hint only when a
`DONE <report.md|txt>` line names an existing report under the watched cwd or
`/tmp`, and one associated GitHub pull URL is confirmed `MERGED` with `mergedAt`
using read-only `gh pr view`. The report is bounded to 64 KiB; its PR URL takes
precedence over output. Ambiguous URLs, missing reports or unavailable GitHub
verification are unknown, with no close hint. No AI statement proves a merge.
The background checker caches failed lookups for five minutes and checks at most
four candidates per pass. It never starts a model or modifies a PR. It never
renames, moves or closes a pane; printed commands are suggestions for Alex.


## Duplicate session claims (v0.7.1)

Herdr can report the same native session id for several live terminals. The
collector preserves an already-known owner and gives additional panes identities
such as `codex:<session>@<terminal>`. If the collision is first seen in a full
snapshot, the initial owner is chosen deterministically. Qualified identities
remain stable if the other pane closes, across moves, and after collector restarts.
Use pane ids for manual declarations when the reported session is ambiguous.
The native `session_identity` is retained separately for trace lookup.

A shared transcript does not prove which pane did something. Shared-session
cards/conversation responses carry a warning; automatic spawn inference and
summaries are suppressed for ambiguous sources. Pane-specific terminal output
is still available read-only. A wrapper resolves its caller through the collector
pane mapping so the parent declaration uses that caller's unique identity.

History keeps closed agents and links. The live graph omits gone agent nodes,
gone endpoint placeholders and empty workspace regions. Workspace live counts
count physical, present panes, independent of historical attention items or
in-process subagent records. Empty workspaces collapse into one expandable group;
opening a workspace/agent history remains possible from that group or the list.

The authorized read-only reconstruction of the existing w4 team, with the exact
sources for each manual reporting line, is in REPORT-v0.7.1.md.
Reporting declarations are configuration in the attention DB, not prompts.
