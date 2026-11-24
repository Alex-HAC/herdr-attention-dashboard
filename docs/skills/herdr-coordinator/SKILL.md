---
name: herdr-coordinator
description: Coordinate visible Herdr workers using Alex's project accounts, role routing, quota rules, and brief/report conventions.
---

Generated from docs/AGENT-ROUTING.md and routing.toml. Read the rules file for rationale;
run `herdr-attention routing show --role ROLE --cwd DIR` for the current eligible pick.

Always spawn workers with `herdr-spawn NAME --role ROLE --brief FILE --reports-to me -- "task"`.
Use visible panes, set model and effort at launch, and keep spawned_by distinct from reports_to.
The wrapper defaults to full permissions; `--sandbox "reason"` is an explicit exception.
Project account restrictions win. Never switch accounts globally or use paid extra credits.

Keep at most 4 panes per tab; overflow into a new named tab.
Coordinators own a tab. Group workers by provider;
use meaningful agent names, titles and role/task tags, never bare numeric tab labels.
The wrapper generates names from role/provider/task. `--name` overrides; names are
slugged, capped at 32 characters and de-duplicated with -2, -3. Default launches
preserve focus; `--focus` is an explicit opt-in. Review quiet layout findings with
`herdr-attention layout check --workspace WORKSPACE`; suggested commands are never
executed by the watcher. A close suggestion needs a report file and confirmed PR merge.
Keep one project/run per workspace or at least a fresh coordinator tab, and preserve focus.
Close only your own finished worker panes after the report/merge, when project rules permit.

| Role | Ordered defaults (subject to accounts, avoid_for, ask_first and quota) |
|---|---|
| coordinator | fable → opus → sonnet |
| architecture | fable → opus |
| independent-review | fable → opus |
| ui-ux-review | fable → opus |
| ui-ux-impl | opus → cursor-opus → cursor-grok |
| backend | codex-sol → cursor-sol → cursor-grok |
| gruntwork | cursor-grok → codex-sol |
| investigation | cursor-sol → cursor-grok → codex-sol |
| pr-review | codex-sol → cursor-sol → opus |
| tests | codex-sol → cursor-grok |
| docs | cursor-grok → codex-sol |
| build-visual-check | agy → codex-sol |

Claude owns architecture, design, UI/UX and final review/merge. Codex/Cursor take
well-defined coding, tests and churn. Avoid lists still apply on fallback.
Keep coordinator turns short: wake on worker events, accept 2–3 line reports,
and give reading/testing/fixing to workers. Below about 50% Claude quota
left, start no new Claude workers; reserve Claude for decisions/review/merge.
Expensive tiers (Astra, business Codex xhigh, routine Fable) need Alex's request.

On a quota wall, preserve the report/branch and stopping point; reshuffle only
at a task boundary through the eligible provider fallback. Never auto-restart.
Write a design/contract, shared project rules, and one brief per worker, covering
scope, clone/branch policy, tests, locks and a `DONE <report>` finish line.
Use fresh clones when project rules allow; workers never merge. Obtain a fresh
independent review sparingly. Project-specific commit/no-push limits take precedence.
Workers report by `herdr agent prompt COORDINATOR "NAME: done|blocked: one line — report PATH"`
when authorized to contact that coordinator. Answer routine questions, record the
recommended decision, and continue; ask Alex only for real product/safety/budget choices.
