"""Alex-editable routing policy. Selection never launches a model or switches accounts."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
import tomllib

from .config import config_dir

KINDS = ('claude', 'codex', 'cursor', 'agy')


class RoutingError(ValueError):
    pass


def load(path: Path | None = None) -> dict:
    override = config_dir() / 'routing.toml'
    path = path or (override if override.exists() else Path(__file__).resolve().parent.parent / 'config/routing.toml')
    try:
        with path.open('rb') as stream:
            data = tomllib.load(stream)
        validate(data)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RoutingError(f'{path}: {exc}') from None
    return data


def strings(value, label):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise RoutingError(f'{label} must be a list of nonempty strings')


def validate(data: dict) -> None:
    if data.get('version') != 1:
        raise RoutingError('version must be 1')
    for section in ('agents', 'roles', 'accounts', 'permissions', 'fallback'):
        if not isinstance(data.get(section), dict):
            raise RoutingError(f'missing [{section}] table')
    layout = data.get('layout', {})
    if not isinstance(layout, dict):
        raise RoutingError('layout must be a table')
    cap = layout.get('max_panes_per_tab', 4)
    if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 32:
        raise RoutingError('layout.max_panes_per_tab must be an integer between 1 and 32')
    if layout.get('group_workers_by', 'provider') not in ('provider', 'role'):
        raise RoutingError('layout.group_workers_by must be provider or role')
    for key in ('coordinator_own_tab', 'no_focus', 'new_tab_when_full', 'forbid_numeric_tab_labels', 'set_title_and_tags_at_spawn'):
        if key in layout and not isinstance(layout[key], bool):
            raise RoutingError(f'layout.{key} must be true or false')
    length = layout.get('pane_name_max_len', 32)
    if not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= 32:
        raise RoutingError('layout.pane_name_max_len must be 1–32')
    abbreviations = layout.get('role_abbrev', {})
    if not isinstance(abbreviations, dict) or not all(isinstance(v, str) and v for v in abbreviations.values()):
        raise RoutingError('layout.role_abbrev must map roles to text')
    for key, fields in (('pane_name_template', {'role', 'provider', 'task'}),
                        ('coordinator_tab_template', {'name'}), ('worker_tab_template', {'group'})):
        if key in layout:
            import string
            value = layout[key]
            if not isinstance(value, str) or not value:
                raise RoutingError(f'layout.{key} must be text')
            if len(value) > 120 or any(field not in fields or spec or conversion
                                       for _, field, spec, conversion in string.Formatter().parse(value) if field is not None):
                raise RoutingError(f'layout.{key} has an unknown template field')
    for name, agent in data['agents'].items():
        if not isinstance(agent, dict) or agent.get('kind') not in KINDS:
            raise RoutingError(f'agents.{name}.kind must be one of {KINDS}')
        for key in ('args', 'avoid_for', 'good_for'):
            strings(agent.get(key, []), f'agents.{name}.{key}')
        for key in ('model', 'effort'):
            if key in agent and (not isinstance(agent[key], str) or not agent[key]):
                raise RoutingError(f'agents.{name}.{key} must be text')
        for key in ('ask_first', 'premium'):
            if key in agent and not isinstance(agent[key], bool):
                raise RoutingError(f'agents.{name}.{key} must be true or false')
    for role, names in data['roles'].items():
        strings(names, f'roles.{role}')
        if not names or any(n not in data['agents'] for n in names):
            raise RoutingError(f'roles.{role} must reference defined agents')
    for name, account in data['accounts'].items():
        if not isinstance(account, dict) or not isinstance(account.get('env'), dict):
            raise RoutingError(f'accounts.{name}.env must be a table')
        if any(k not in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR') or not isinstance(v, str) or not v
               for k, v in account['env'].items()):
            raise RoutingError(f'accounts.{name}.env may only name CODEX_HOME or CLAUDE_CONFIG_DIR paths')
    projects = data.get('project', [])
    if not isinstance(projects, list):
        raise RoutingError('project must be an array of tables')
    for project in projects:
        if not isinstance(project, dict) or not isinstance(project.get('match'), str) or not project['match']:
            raise RoutingError('each project needs a match path')
        strings(project.get('accounts'), 'project.accounts')
        if any(n not in data['accounts'] for n in project['accounts']):
            raise RoutingError('project.accounts references an undefined account')
    if not isinstance(data['permissions'].get('yolo'), dict):
        raise RoutingError('permissions.yolo must be a table')
    for provider in KINDS:
        strings(data['permissions'].get('yolo', {}).get(provider), f'permissions.yolo.{provider}')
    for provider, chain in data['fallback'].items():
        strings(chain, f'fallback.{provider}')
        if provider not in KINDS or any(p not in KINDS for p in chain):
            raise RoutingError('fallback references an unknown provider')


def allowed_accounts(data: dict, cwd: str) -> list[str]:
    cwd = str(Path(cwd).expanduser().resolve())
    matches = []
    for project in data['project']:
        pattern = os.path.expanduser(project['match']).rstrip('/')
        if fnmatch.fnmatchcase(cwd, pattern) or (not any(c in pattern for c in '*?[') and cwd.startswith(pattern + '/')):
            matches.append((len(pattern.rstrip('*')), project['accounts']))
    if not matches:
        raise RoutingError(f'no project account policy matches {cwd}; add [[project]] to routing.toml')
    return max(matches, key=lambda pair: pair[0])[1]


def pick(data: dict, *, role: str, cwd: str, walls: list[dict] = (), agent: str | None = None,
         kind: str | None = None, model: str | None = None, effort: str | None = None) -> dict:
    accounts = allowed_accounts(data, cwd)
    if agent and agent not in data['agents']:
        raise RoutingError(f'unknown routing agent {agent!r}')
    if kind and kind not in KINDS:
        # --kind accepts routing names too for compatibility with the brief.
        if kind in data['agents'] and not agent:
            agent, kind = kind, None
        else:
            raise RoutingError(f'unsupported kind {kind!r}')
    if agent and kind and data['agents'][agent]['kind'] != kind:
        raise RoutingError('--agent and --kind select different providers')
    explicit = bool(agent or kind)
    candidates = [agent] if agent else list(data['roles'].get(role, []))
    if not candidates and not kind:
        raise RoutingError(f'unknown role {role!r}; choose a defined role or explicit --kind')
    if kind:
        candidates = [n for n in candidates if data['agents'][n]['kind'] == kind]
        candidates += [n for n, a in data['agents'].items() if a['kind'] == kind and n not in candidates]
    skipped = []
    # Role order remains authoritative; fallback only permits provider transitions
    # when a preceding candidate was quota-walled, never invents a design fallback.
    fallback = None
    for name in candidates:
        spec = data['agents'][name]
        provider = spec['kind']
        account = next((a for a in accounts if a.split('-')[0] == provider), None)
        reason = None
        wall = next((w for w in walls if w['provider'] == provider and
                     (not w.get('premium_only') or spec.get('premium'))), None)
        if not account:
            reason = 'account not allowed for project'
        elif spec.get('ask_first') and agent != name:
            reason = 'ask_first'
        elif not explicit and role in spec.get('avoid_for', []):
            reason = f'avoids {role}'
        elif not explicit and wall:
            reason = 'quota-walled'
            if fallback is None:
                fallback = [provider, *data['fallback'].get(provider, [])]
        elif not explicit and fallback is not None and provider not in fallback:
            reason = 'outside provider fallback chain'
        if reason:
            skipped.append(f'{name}: {reason}')
            continue
        return {'agent': name, **spec, 'account': account,
                'env': {k: os.path.expanduser(v) for k, v in data['accounts'][account]['env'].items()},
                'model': model or spec.get('model'), 'effort': effort or spec.get('effort'),
                'why': ('explicit override' if explicit else f'first eligible agent for {role}') +
                       (f"; skipped {'; '.join(skipped)}" if skipped else '')}
    raise RoutingError(f'no eligible agent for {role}: ' + '; '.join(skipped))


def coordinator_skill(data: dict, rules_path: Path) -> str:
    layout = data.get('layout', {})
    rows = '\n'.join(f'| {role} | {" → ".join(names)} |' for role, names in data['roles'].items())
    return f'''---
name: herdr-coordinator
description: Coordinate visible Herdr workers using Alex's project accounts, role routing, quota rules, and brief/report conventions.
---

Generated from {rules_path} and routing.toml. Read the rules file for rationale;
run `herdr-attention routing show --role ROLE --cwd DIR` for the current eligible pick.

Always spawn workers with `herdr-spawn NAME --role ROLE --brief FILE --reports-to me -- "task"`.
Use visible panes, set model and effort at launch, and keep spawned_by distinct from reports_to.
The wrapper defaults to full permissions; `--sandbox "reason"` is an explicit exception.
Project account restrictions win. Never switch accounts globally or use paid extra credits.

Keep at most {layout.get('max_panes_per_tab', 4)} panes per tab; overflow into a new named tab.
Coordinators own a tab. Group workers by {layout.get('group_workers_by', 'provider')};
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
{rows}

Claude owns architecture, design, UI/UX and final review/merge. Codex/Cursor take
well-defined coding, tests and churn. Avoid lists still apply on fallback.
Keep coordinator turns short: wake on worker events, accept 2–3 line reports,
and give reading/testing/fixing to workers. Below about {data.get('policy', {}).get('frugal_below_claude_pct_left', 50)}% Claude quota
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
'''
