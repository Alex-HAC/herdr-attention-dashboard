# Agent routing: who does what

_Draft 13 Nov 2026, written by Herdr-Bot from Alex's own agent traces (read-only). **Alex owns this file: edit freely.**
The machine-readable table is [`config/routing.toml`](../config/routing.toml); keep them in step.
Tags: **[stated]** = Alex said it (paraphrased, date given). **[inferred]** = Herdr-Bot's reading of what worked; change it if wrong._

## The rule in one line

**Claude thinks, the others type.** Claude (Fable / Opus) owns design, architecture, UI/UX and the final review/merge; Codex and
Cursor do the well-defined coding, content churn, tests and cleanup, because they have far more tokens. Every choice is
weighed against **how much quota is left right now**.

## Global rules and automatic linking backup (v0.7.2)

The single global rules source is `~/.config/agents/AGENTS.md`. Edit that file
when changing global rules. `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
`~/.codex-personal/AGENTS.md`, `~/.gemini/GEMINI.md` and
`~/.config/opencode/AGENTS.md` are symlinks to it; do not edit separate copies.
Cursor CLI has no global rules file. For every provider, including Cursor,
`herdr-spawn` prepends `Global rules: read ~/.config/agents/AGENTS.md` to the first
prompt, before the brief pointer and task. It does not edit the brief or rules.

Always use `herdr-spawn` and declare reporting lines. The dashboard also has a
passive backup for workers started another way: specific first-prompt brief or
coordinator transcript evidence can establish an **inferred** reporting line.
These appear dashed and fainter, with evidence and confidence. Declarations
always win. `team link` promotes an inferred line to declared; `team unlink`
permanently suppresses automatic reporting links for that worker identity.
Names, titles and sharing a tab alone never establish supervision. See
[relationships.md](relationships.md#passive-reporting-fallback-v072).

## What Alex judges on

| Criterion | What he means | Evidence |
|---|---|---|
| Cost / quota | Claude is scarce and expensive; Codex and Cursor have more tokens. Use session limits only, never extra/paid credits. | [stated] 8 Oct "codex is gonna run out… get some Claude terminals"; 22 Oct "Don't use extra credits, only my session limit… Codex since we have a lot more tokens there"; 5–6 Nov "you're not relying on codex enough"; 13 Nov 22:31 "53% used for the next 4 hrs, be more mindful and selective" |
| Task type | Claude: UI/UX (reviewing **and** generating it), architecture, design. Codex: writing code, churning through content, well-defined but possibly complex coding. Codex is weak at UI/UX and architecture. | [stated] 30 Oct "codex sucks at frontend"; 6 Nov "claude… better at generating [UI] and making it look and feel much nicer"; 13 Nov "Dont use codex for ui/ux reasoning / design" |
| Design values | Modularity, maintainability, scalability, and **optimising for agentic workflows** (skills, briefs, contracts agents can follow). | [stated] 13 Nov to Fable; 26 Oct "add skills… so agents easily know how to do / use all this" |
| Effort per job | Pick model *and* effort per job. Gruntwork: Codex Sol 6.2 on high (was Sol 6.1 until 17 Nov) is best per token. Small, well-specified UI slices can go to Opus at low/medium effort. Expensive tiers (Codex Astra, xhigh) only when really needed. | [stated] 4 Nov "for gruntwork, sol6.1 on high is best-token-wise"; 6 Nov "lower effort opus 5.5… as long as its defined well"; 13 Nov "astra uses it up rlly fast so only if its really needed" |
| Independent review | A *fresh* Fable (xhigh) agent for plan/architecture reviews and course checks, used sparingly. | [stated] 31 Oct, 4 Nov, 5 Nov, 11 Nov ("dont overdo this"), 12 Nov (Fable UI/UX review before a pre-release) |
| Parallelism | Yes: spawn more workers when it speeds things up, in visible Herdr panes/tabs he can watch, not hidden subagents. | [stated] 7 Oct, 16 Oct, 13 Nov 14:56 "use cursor workers to parallelize"; 6 Oct "start the agents in this herdr tab, so I can keep tabs on them" |
| Autonomy | Workers and coordinators answer each other's questions and run to completion; take the recommended/safe option and note it. Only real product, safety or budget decisions go to Alex. | [stated] 26 Oct "Do recs, always just go"; 4 Nov "keep answering questions… unless you really really really need me"; night rules 13 Nov "decide, note the decision, go on" |
| Permissions | **Every agent starts with yolo/full permissions** (Codex `--yolo`, Claude `--dangerously-skip-permissions`, Cursor `--yolo --trust`, agy its equivalent). Sandbox only for a rare, explicit reason such as a test sandbox, and say why. Project hard limits still apply. | [stated] 4 Nov (gruntwork), **13 Nov for all agents** |
| Account | Per project, chosen at launch through env (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`); never switch accounts globally. Work projects use the work accounts. This dashboard's v0.7 build runs on the **work Codex account** (approved 13 Nov); its earlier builds used personal accounts. | [stated] standing rule; 13 Nov "you can start a sol high worker on this on the work account" |

## Roles and default picks

| Role | Default | Fallback when quota-walled | Avoid |
|---|---|---|---|
| **Coordinator** (plans, splits, reviews, merges) | Claude Fable 5.2 high, or Opus 5.5 high | Opus 5.5 → Sonnet as *router only* (no design) | Codex as coordinator of UI-heavy work [inferred] |
| **Architect / design doc** | Claude Fable 5.2 (high/xhigh) | Opus 5.5 high | Codex, Cursor Grok |
| **UI/UX implementation** | Claude Opus 5.5 low–medium, one view per brief, captures required | Cursor `claude-opus-5-thinking-high` (premium) → Cursor Grok on a strict layout/wording spec the coordinator wrote | Codex for anything that needs taste |
| **Backend / core code, refactors** | Codex `gpt-6.2-sol` high, `--yolo` | Cursor `gpt-5.6-sol-high` → Cursor Grok 4.7 high | Claude (too costly) |
| **Gruntwork** (cleanup, porting, docs churn, screenshots, fixtures) | Cursor Grok 4.7 high or Codex Sol high | the other one | Claude |
| **Investigation / as-is mapping** (no code) | Cursor `gpt-5.6-sol-high` or Grok | Codex Sol high | — |
| **PR review → "ready"** | Codex Sol high review worker; coordinator merges | Opus review | Self-review; Sonnet peer review [stated in a 7 Nov routing table] |
| **Independent plan / course review** | Fresh Claude Fable xhigh pane, read-only, writes a report | Opus high | Using it for every step |
| **Build / visual checks** | agy (Antigravity) with Opus reviewing [stated 11 Nov] | Codex or the coordinator | Relying on agy: it hit capacity twice and blocks on file-approval dialogs [inferred] |

## Quota-aware rules (apply all the time)

1. **Coordinator thrift.** The coordinator keeps its own turns short: wake only on worker events (report written, review verdict,
   quota stop), no polling loops, no restating; reports to it are 2–3 lines. Reading, testing and fixing go to workers. [stated 31 Oct, 6 Nov, 13 Nov]
2. **Below ~50% of a Claude window** (or Alex says "be frugal"): no new Claude workers, Claude only reviews/merges/decides. [inferred threshold, stated behaviour]
3. **Reshuffle on a quota wall.** When a worker prints a limit/capacity/credits error: write down where it stopped, move its job to the next
   provider in the fallback chain (keeping the role's "avoid" list), restart from its report/branch, and tell the coordinator. Typical chain:
   Codex out → Cursor (Sol premium, then Grok) → Claude only for what must not leave Claude. Cursor premium out → Grok/default models still work. [stated by behaviour, 13 Nov]
4. **Never use paid extra credits or spend** without Alex. A free reset he offers is fine to apply. [stated 22 Oct, 13 Nov]
5. **Effort changes cost tokens** (full history re-read). Set effort at start; at a task boundary prefer `/clear` or a fresh pane. [stated 30 Oct]
6. **Expensive tiers on request only:** Codex Astra, any xhigh on the business Codex, Fable for routine work.

## How work is handed out (the Fable coordinator pattern, 13 Nov)

- Coordinator writes a design/contract first, then **one brief file per worker** plus one shared rules file
  (`*-common-<date>.md`: clone folder, branch/PR rules, tests, locks, never contact shared servers, final `DONE <report>` line).
- Workers work in **fresh clones**, open PRs, never merge; a review worker marks PRs ready; the coordinator merges.
- Workers may change shared fixtures/contracts but record it in their report.
- Workers report with `herdr agent prompt <coordinator> "<name>: done|blocked: <one line> — report <path>"`, never with an interactive dialog.
- Skills and method files are what keep Codex/Cursor on track: every working method goes into a skill with exact commands. [stated 31 Oct]

## Permissions default

The spawn wrapper adds the yolo flags from `[permissions.yolo]` to every launch. `--sandbox "<reason>"` opts out and records the reason
on the agent's card. [stated, Nov 13]

## Herdr layout and labeling

Goal: Alex can open Herdr (or the dashboard) and see at a glance who is who and what each tab is for. [stated, Nov 13: "not too many
panes per tab and things properly labeled so its easy to find and monitor things also from herdr"]

What the layout looked like on 13 Nov 23:30 (why these rules exist): the `codex` tab held **7 panes**; 5 agent panes had **no name**
(including the Fable coordinator `w4:p3B`, findable only by its tab); one tab was labelled just `1`; several pane titles read
"Continue the task", which says nothing; workspace `Release PR review` held four different coordinator tabs from different runs.
The good parts to keep: Fable's split into `Fable coordinator` / `codex` / `cursor` tabs and the named `codex-b1`, `codex-k1` panes.

| # | Rule | Tag |
|---|---|---|
| 1 | **At most 4 panes per tab.** A 5th worker goes into a new tab (`codex-2`, `cursor-2`), never a further split. | stated (not too many panes); the number 4 is inferred |
| 2 | **One workspace per project or run**, labelled with it (`release-1.2-night`, `herdr-dashboard`). A new run with a new coordinator gets a new workspace or at least a new tab, not a pane in an old run's tab. | inferred |
| 3 | **The coordinator gets its own tab**, alone, labelled `<coordinator-name> coordinator` (e.g. `Fable coordinator`). | inferred from Fable's layout |
| 4 | **Workers are grouped in tabs by provider or role** (`codex`, `cursor`, `review`, `ui`). Tab labels say what the tab is for, never a bare number. | inferred from Fable's layout; stated "properly labeled" |
| 5 | **Every agent pane has a name** `<role>-<provider>-<short-task>`, lowercase, ≤ 32 chars, Herdr's `[a-z][a-z0-9_-]` (e.g. `wrk-codex-catalog`, `rev-codex-prs`, `ui-opus-library`, `coord-fable`). Rename when the task changes. | stated "properly labeled"; scheme inferred |
| 6 | **Set name, terminal title and hat tags at spawn** (`role`, `task`, `parent`, `self`), so Herdr's sidebar, the pane title and the dashboard all agree. | inferred |
| 7 | **Clean up finished panes**: once a worker's report is in and its PR merged (or the job is dropped), the coordinator closes the pane; empty tabs are closed too. The report file and PR are the record, so nothing is lost. | stated by behaviour (Alex asked Fable 13 Nov to close panes no longer needed); timing inferred |
| 8 | **Never steal focus**: spawn and create tabs with `--no-focus`; never focus or move Alex's view. | stated (standing rule: don't disturb him) |
| 9 | **A full tab means a new tab**, created next to the old one with the same group label plus a number. | inferred |

## Editing

Change `config/routing.toml` for defaults the spawn wrapper uses; change this file for the reasoning. The wrapper and coordinator
skill read the TOML, so a change there takes effect on the next spawn.
