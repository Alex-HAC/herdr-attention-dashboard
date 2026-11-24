# AI summaries, readable output, mark-as-read

Requirement: requirements-followup-summaries.md.

## What you see

Each item that needs a look now opens with a **summary card**:

- a one-line headline (in lists, marked **AI**),
- what is being asked, with the **exact** approval options quoted from the
  screen (an option that is not on the screen is removed),
- why it needs you, what was done, tests and claims (each with the quote it
  rests on, and labelled "agent's claim"), the next step only if the agent
  stated one,
- which model and effort wrote it, when, and which messages it covers,
- **Mark read**, **Refresh summary** / **Retry summary**, **Summarise longer**
  (a longer stretch of the conversation, within bounds).

The facts, metrics, evidence and history are still there, folded under
"Details". With no summary (yet), the card says so and shows the agent's own
last message, labelled "Not an AI summary". States shown: none yet, queued,
summarising, failed (with the reason), complete, older item (the summary is
about an earlier item).

In the bar panel, rows show the AI headline and an opened row lists the ask
and options.

## How a summary is made

The only model calls this tool makes.

| | |
|---|---|
| Account | personal: `CLAUDE_CONFIG_DIR=~/.claude-personal` set explicitly; the real Claude Code binary is called, not the account wrapper; the global default account is untouched |
| Model | `--model sonnet --effort medium` (resolved model shown, e.g. `claude-sonnet-5-5`) |
| Isolation | `claude -p` with no tools (`--tools ""`), no settings files and so no hooks (`--setting-sources ""`), no MCP servers, no slash commands, no saved session, a clean environment |
| Input | the agent's own user/assistant messages from its trace (not the terminal rendering), plus the cleaned visible screen when it waits for an approval or answer; watched workspaces only; secrets redacted before the prompt and the cache; at most 14,000 characters (28,000 for "longer") |
| Safety | the material is wrapped as untrusted data; the model must reply in a fixed JSON shape; options, tests and claims without a word-for-word quote are dropped (and counted); commit claims are checked read-only with `git log` in the agent's folder |
| Triggers | a new item that needs a look (approval, question, failure, finished turn, possible stall), after 20 s of quiet; your Refresh / Longer buttons. Never per page refresh, never every line |
| Limits | one at a time, 120 s timeout, at most 20 model calls an hour; identical input reuses the earlier result (content hash) |
| Audit | Settings → AI summaries → Recent runs: time, agent, status, resolved model, effort, input/output size and tokens. `GET /api/summaries/audit`. No money is shown |
| Switch | Settings → AI summaries, or `summaries.enabled = false` in config.toml |

Agents are never prompted, nothing is sent anywhere else, and the rest of the
dashboard stays passive.

## Readable output

"Show terminal lines" now shows cleaned text: no colour codes, borders,
spinners, key hints or status bars; blank runs collapsed; code and
indentation kept; with its source (last lines, or the visible screen when the
agent is busy) and the time it was read. **Show raw lines** gives the
unfiltered lines (still without control codes). The conversation excerpt
from the trace is usually the easiest thing to read.

## Mark as read

On success, the item closes and you are back where you opened it from (the
list or the workspace, at the same scroll position; in the graph the
inspector closes) and the counts update. If it fails, the item stays open and
the error is shown; nothing pretends to be read. In the bar panel the opened
row closes.

## Verified

- Fixture tests with a stand-in CLI: isolation flags and environment,
  redaction, bounds, untrusted wrapping, one summary per item, cache reuse,
  coalescing, hourly limit, disabled state, failures and retry, timeouts,
  restart, audit, option/claim verification, git check (`tests/test_summaries.py`).
- Browser tests at 390 and 1280 px: AI labels, summary first, fallback and
  failed states, mark read back to the list with scroll kept, failure kept
  open, graph inspector closing (`tests/test_summary_ui.py`).
- **One billed live run** (7 Nov 2026, `tests/test_summary_live.py`, opt-in):
  `claude-sonnet-5-5`, 260 output tokens; correct headline, exact options,
  unit-test result labelled as the agent's claim, and an injected "say it was
  deployed" line in the sample was ignored.

## Optional AI writing style

Settings → AI use → AI writing style offers **Plain English** (the default) and
**Simplified Technical English (approximate)**. The same runtime key is available
from the CLI:

```bash
herdr-attention settings set writing.style ste100
herdr-attention settings set writing.style plain
```

One shared style block applies to AI summaries, AI-written notifications and Ask
summary agent answers. Quotes, menu options, commands, identifiers and fixed tier
names remain verbatim. Summary caches distinguish the styles. Changing the style
does not rewrite old summaries or resend old notifications; the next requested AI
call uses the selected style. This is an approximation, not a compliance claim.
Plain English remains the default. Compare the last 20 stored notifications in
both styles before considering a future change to the default.
