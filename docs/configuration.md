# Configuration

File: `~/.config/herdr-attention/config.toml` (created from
`config/config.example.toml`, which documents every key). Restart after
editing: `systemctl --user restart herdr-attention`.

Settings you change in the panel, the phone page or with
`herdr-attention settings set` are stored in the state database and win over
the file. `herdr-attention settings reset` drops them.

## Changeable at runtime

| Key | Default | Phone may change |
|---|---|---|
| `watch.enabled_workspaces` | `[]` | yes (since v0.2, via the chooser) |
| `watch.muted_workspaces` | `[]` | yes |
| `events.question` / `approval` / `failure` / `completion` / `stall` | `true` | yes |
| `stall.minutes` | `15` | yes |
| `stall.per_workspace` | `{}` | no |
| `presence.phone_delay_minutes` | `5` | yes |
| `presence.mode` | `auto` (`always`, `never`) | yes |
| `ui.parked_after_minutes` | `60` | yes |

Example: `herdr-attention settings set stall.minutes 30`

## File only

| Key | Default | Meaning |
|---|---|---|
| `herdr.socket` | Herdr default | Herdr socket path |
| `stall.output_poll_seconds` | `60` | How often a working agent's output is fingerprinted; `0` = never |
| `failure.when_gone_while` | `["working", "blocked"]` | Disappearing in these states counts as a failure |
| `classify.approval_rules` / `question_rules` | see example | Substrings of Herdr rule ids |
| `presence.heartbeat_stale_seconds` | `180` | No report for this long → presence unknown |
| `presence.unknown_means` | `away` | What unknown counts as for phone alerts |
| `notify.*` | off | ntfy settings, rate limit, summary size, priorities |
| `history.retention_days` | `7` | Resolved or read events older than this are deleted |
| `output.lines` / `output.redact` | `40` / patterns | What "Show output" returns |
| `api.socket` | `$XDG_RUNTIME_DIR/herdr-attention/api.sock` | Local API |
| `phone.*` | off | Phone page listener |
| `explain.backend` | `none` | Not built yet |
| `keyring.service` | `herdr-attention-dashboard` | GNOME keyring service name |
| `ui.acknowledge` / `ui.group_attention_first` | `true` | Proposed features, switchable |

## Traces (file only)

| Key | Default | Meaning |
|---|---|---|
| `traces.enabled` | `true` | Read trace metrics for agents in watched workspaces |
| `traces.claude_dirs` | `~/.claude-personal/projects`, `~/.claude/projects` | Claude project folders (personal and work) |
| `traces.codex_dirs` | `~/.codex/sessions` | Codex session folder |
| `traces.poll_seconds` | `30` | How often traces are checked for new lines |
| `traces.max_bytes_per_pass` | `16777216` | Read budget per trace per pass |
| `traces.conversation_max` | `50` | Largest "last N" conversation request |

`stall.output_poll_seconds` (default 60) now also decides whether stalls are
detected at all: with `0` there are no stall flags, because status alone is
never treated as evidence.

## Summaries (file; `summaries.enabled` also in the dashboard)

| Key | Default | Meaning |
|---|---|---|
| `summaries.enabled` | `true` | Master switch; off means no model calls, including the Summarize button |
| `summaries.auto` | `false` | Automatic summaries when an agent finishes; off by default |
| `summaries.account` | `codex-personal` | Codex · Personal / Work, or Claude · Personal / Main |
| `summaries.model` / `effort` | `gpt-6.2-sol` / `low` | Model and effort for summary calls |
| `summaries.claude_config_dir` | `~/.claude-personal` | Personal Claude home (process-scoped) |
| `notify.enabled` | `true` | Send phone alerts when ntfy is configured |
| `notify_gate.enabled` | `true` | Cheap check before a notification is sent |
| `notify_gate.account` / `model` / `effort` | `codex-personal` / `gpt-6.2-sol` / `low` | Account used for the gate |
| `notify_tiers.preset` | `normal` | quiet / normal / close_tabs / everything / custom |
| `notify_tiers.<tier>` | see defaults | Per-tier on/off. Waiting on you, stalls/failures, and FYI finished are on; coordinator, milestones, and verbose are off |
| `notify_tiers.<tier>_delivery` | `ntfy` | `ntfy` (phone), `desktop`, or `dashboard` (no push) |
| `notify_tiers.<tier>_gate` | see defaults | `check` / `always` / `off`. Waiting on you is always-send (the AI may only rewrite) |
| `notify_tiers.quiet_hours` | `false` | When on, only Waiting on you is sent during `quiet_start`–`quiet_end` (Europe/Amsterdam) |
| `notify_tiers.watch_closely` | `[]` | Agent identities that notify on every tier |

CLI: `herdr-attention notify config` and `herdr-attention notify preset <name>`.
| `summaries.claude_bin` | auto | The real Claude Code binary (never the account wrapper) |
| `summaries.max_per_hour` | `20` | Model calls per hour at most |
| `summaries.coalesce_seconds` | `20` | Quiet time before an automatic summary |
| `summaries.timeout_seconds` | `120` | Per call |
| `summaries.max_input_chars` | `14000` | Material per summary (double for "longer") |
| `summaries.entries` / `longer_entries` | `20` / `60` | Trace messages used |
| `summaries.check_git` | `true` | Check commit claims with `git log` (read-only) |

## Secrets

Only in the GNOME keyring, never in files:

```bash
herdr-attention secret set ntfy-token
herdr-attention secret set dashboard-passphrase
herdr-attention secret status ntfy-token
```

They are stored with `service=herdr-attention-dashboard key=<name>` and are
never printed.

## Files

| Path | What |
|---|---|
| `~/.local/share/herdr-attention/app/` | installed code (`app.previous/` = rollback copy) |
| `~/.local/bin/herdr-attention` | command |
| `~/.config/herdr-attention/config.toml` | config |
| `~/.local/state/herdr-attention/state.db` | history, identities, overrides (no output text) |
| `~/.config/systemd/user/herdr-attention.service` | user service |
| `~/.config/omarchy/plugins/alexvdb.herdr-attention/` | desktop plugin |
| `~/.local/share/herdr-attention/backup/` | `shell.json` backups from each install |


## Agent routing and quota walls (v0.7.0)

The shipped `config/routing.toml` is installed with the app. A complete
`~/.config/herdr-attention/routing.toml` overrides it when present. The loader
validates tables, agent references, provider names, account paths and role lists
and reports the file and cause on an invalid policy. Edit the TOML and
[AGENT-ROUTING.md](AGENT-ROUTING.md) together. Project rules match cwd (including
subdirectories); the most specific match wins. Unmatched projects fail closed.
The dashboard project currently permits **codex-work only**, as approved for v0.7.
Explicit provider/model choices cannot bypass project account restrictions.

```bash
herdr-attention routing show
herdr-attention routing show --role backend --cwd ~/Projects/herdr-attention-dashboard
herdr-attention routing clear --provider codex
```

Automatic selection follows `[roles]` order, skips `avoid_for` and `ask_first`
entries, checks allowed accounts and active quota walls, and honors `[fallback]`
provider transitions. An explicit agent/kind may override role/quota exclusions;
explicit model/effort override the chosen model/effort. No automatic pick can
use an `ask_first` agent. A role can have no eligible provider: design is parked
rather than silently sent to a provider excluded by the role/account policy.

Quota detection reads only the visible output of watched agents, including idle
or blocked agents, on the existing output-poll interval. It records provider,
source agent and expiry in SQLite, never output text. Cursor premium-only errors
leave non-premium Cursor routes eligible. Walls expire after 3600 seconds by
default or can be cleared by the local CLI. An unchanged screen cannot extend
its wall; there is no automatic restart or reassignment. The message is evidence
of a wall, not a measured remaining quota balance.

Additional settings in config.toml (`sidebar.enabled` can also be set through the local CLI):

```toml
[routing]
quota_wall_seconds = 3600 # 0: human clear required
[sidebar]
enabled = true
```

`[permissions.yolo]` in routing.toml supplies each provider's full-permission
flags; installed Codex/Claude/Cursor/agy help was checked for v0.7. `--sandbox
"reason"` opts out with explicit native sandbox/permission flags. Subscription
launches reject inherited API-key/provider override variables instead of risking
paid extra credits. Account variables are cleared and set on the child only.

Coordinator launches get a short pointer to a generated, launch-local skill
built from current routing defaults and the routing rules. The shipped reference
is [skills/herdr-coordinator/SKILL.md](skills/herdr-coordinator/SKILL.md). It is
never installed into shared Claude skills. Per-launch files contain no auth data.


The installer links the native Herdr plugin id `herdr-attention`. Herdr requires
an enabled registered plugin before accepting `agent.view.set`; activation is
checked before any display tokens are published. Plugin actions enable/clear the
tree using the collector's local setting. Disabling/unlinking the native plugin
also prevents view activation. No Herdr server restart is needed.


`[layout]` in routing.toml controls the pane cap (default 4), provider/role
worker groups, overflow labels, coordinator tabs, and generated name templates.
The launcher counts all panes, including shells, and scopes group reuse to the
caller's workspace. `--name` overrides the positional/generated name. Names are
slugged and de-duplicated with numeric suffixes; an omitted name uses the role
abbreviation, provider and task. Full tabs overflow even with `--split`.
Titles and role/team/task tags apply only to the new pane; focus is preserved
unless `--focus` explicitly opts in.
Workspace-per-run and closing finished workers remain coordinator conventions,
not background dashboard actions. Existing panes are never renamed or closed.


Read-only layout findings are available at `GET /api/layout?workspace=W` (the
same authentication and watch filtering as `/api/view`). `herdr-attention layout
check [--workspace W] [--json]` and `doctor` consume these quiet findings; they
have no effect on attention health/notifications. Completion evidence is checked
in a background worker via bounded local report reads and cached `gh pr view`
requests. Missing credentials/network/report/unique PR evidence produces no
close suggestion. No authentication data or model call is needed by the layout
rules themselves; GitHub verification uses the existing gh login read-only.

## Passive reporting fallback (v0.7.2)

```toml
[inference]
enabled = true
```

This file-only setting enables bounded first-prompt brief and transcript
evidence reads for watched, live, unambiguous sessions. `traces.enabled = false`
also disables inference. The existing trace directories/account configuration
are reused; no model or agent-control API is used. Details, bounds and evidence
rules are in [relationships.md](relationships.md#passive-reporting-fallback-v072).

Declared links always win. `team unlink` persists a worker-identity inference
veto across restarts; explicit `team link` remains available. The local-only
`GET /api/inference/check?ignore_declared=REF,REF` endpoint (CLI: `inference check
--ignore-declared REF`) is a dry check and never changes those declarations.

Every `herdr-spawn` launch now prepends a first-prompt pointer to
`~/.config/agents/AGENTS.md`, including when no task/brief was supplied. This
applies to all providers; Cursor otherwise has no global rules-file support.
The wrapper never modifies that rules file, its symlinks or shared settings.

## Agent quotas (v0.7.3)

The phone overview shows a compact strip of subscription quotas mirrored from
the Omarchy Agents panel. The collector only reads
`~/.local/state/omarchy/agents/usage/` (and `order.json` beside it); it never
runs `omarchy-agent-usage-update`, refreshes tokens, or switches accounts.

Optional overrides in `config.toml`:

```toml
[quotas]
usage_dir = ""
order_path = ""
timezone = "Europe/Amsterdam"
```

Empty paths use the panel's default state directory. Reset times are shown in
the timezone above (Europe/Amsterdam by default). Emails and credentials in
those records are dropped before the API or page sees them.

## Attention tiers and replies (v0.7.5)

The overview card groups agents into *Waiting on you*, *Waiting on a
coordinator*, *FYI / finished* and *Might want a look*. This is computed
from Herdr status, permission-prompt rules, the last assistant message,
and `reports_to` links. A summary-only ask is dropped once the agent has
been idle longer than `ui.parked_after_minutes` (default 60), unless the
last message itself is a question to you. `attention.ai_classify` exists
for an optional cheap-model assist on unclear items and is off by default.

Replies use `POST /api/replies` (login + CSRF on the phone dashboard). A
body without `confirm: true` returns a preview only. Sending a prompt
never happens automatically. Working agents default to a queue that
flushes when they are idle or done; a blocked approval screen cannot
take a prompt until that screen is dismissed.
