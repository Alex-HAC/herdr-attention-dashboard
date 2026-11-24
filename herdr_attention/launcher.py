"""Visible Herdr workers with child-only account/link env and session-bound tags.

Uses pane.run because Herdr 0.9.1 agent.start cannot set a child environment.
The interactive pane shell never receives launch variables. No shared settings
or account selection is changed. Tag validation credits Algorant/herdr-agent-tree (MIT).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import uuid
from typing import Callable

from .linking import session_hash

AMBIGUOUS = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX',
             'ANTHROPIC_BASE_URL', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CURSOR_API_KEY', 'CURSOR_API_ENDPOINT')
KINDS = ('claude', 'codex', 'cursor', 'agy')
EXECUTABLE = {'claude': 'claude', 'codex': 'codex', 'cursor': 'cursor-agent', 'agy': 'agy'}
CLEAR_ENV = ('HERDR_PARENT_PANE', 'HERDR_PARENT_SESSION', 'HAT_RUN_ID', 'HAT_KIND', 'HAT_API_SOCKET',
             'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'CLAUDECODE')


class LaunchError(RuntimeError):
    pass


def plan(*, name: str, kind: str, env: dict, config_dir: str | None, direction: str, cwd: str | None,
         agent_args: list[str], selection: dict | None = None, permissions: list[str] | None = None,
         sandbox: str | None = None, role: str | None = None, brief: str | None = None,
         team: str | None = None, task: str | None = None,
         tab: bool = False, api_socket: str | None = None, caller: dict | None = None, parent_identity: str | None = None,
         layout_policy: dict | None = None, existing_tabs: list | None = None,
         existing_panes: list | None = None, explicit_split: bool = False, focus: bool = False) -> dict:
    if env.get('HERDR_ENV') != '1' or not env.get('HERDR_PANE_ID'):
        raise LaunchError('run this from inside a Herdr pane (HERDR_ENV and HERDR_PANE_ID are needed)')
    from .layout import unique_name
    name = unique_name(name)
    if kind not in KINDS:
        raise LaunchError(f'kind must be one of {", ".join(KINDS)}')
    if direction not in ('right', 'down'):
        raise LaunchError('direction must be right or down')
    clashes = [k for k in AMBIGUOUS if env.get(k)]
    if clashes:
        raise LaunchError(f'refusing: {", ".join(clashes)} set; subscription account would be ambiguous')
    if sandbox is not None and (not sandbox.strip() or len(sandbox) > 80):
        raise LaunchError('--sandbox needs a reason of 1–80 characters')
    from .layout import choose
    placement = choose(layout_policy, name=name, role=role or 'worker', provider=kind,
                       workspace=env.get('HERDR_WORKSPACE_ID'), caller_pane=env['HERDR_PANE_ID'],
                       caller_tab=env.get('HERDR_TAB_ID'), tabs=existing_tabs or [], panes=existing_panes or [],
                       new_tab=tab, explicit_split=explicit_split)
    tab = placement['create_tab']
    split = (['herdr', 'tab', 'create', '--workspace', env['HERDR_WORKSPACE_ID'],
              '--label', placement['label'], '--no-focus'] if tab else
             ['herdr', 'pane', 'split', placement['target'], '--direction', direction, '--no-focus'])
    if focus:
        split = ['--focus' if part == '--no-focus' else part for part in split]
    if cwd:
        split += ['--cwd', str(Path(cwd).expanduser().resolve())]
    child_env = dict((selection or {}).get('env', {}))
    if kind == 'claude':
        chosen = config_dir or child_env.get('CLAUDE_CONFIG_DIR') or (str(Path.home() / '.claude') if selection else env.get('CLAUDE_CONFIG_DIR'))
        if not chosen:
            raise LaunchError('refusing: no Claude account given; pass --config-dir or set CLAUDE_CONFIG_DIR')
        chosen = os.path.expanduser(chosen)
        expected = child_env.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))
        if selection and chosen != expected:
            raise LaunchError('--config-dir conflicts with the project account policy')
        child_env['CLAUDE_CONFIG_DIR'] = chosen
    elif config_dir:
        raise LaunchError('--config-dir is only for Claude agents')
    if kind == 'codex':
        child_env.setdefault('CODEX_HOME', str(Path.home() / '.codex'))
    run_id = str(uuid.uuid4())
    child_env.update(HERDR_PARENT_PANE=env['HERDR_PANE_ID'], HERDR_PARENT_SESSION=_identity(caller) or '',
                     HAT_RUN_ID=run_id, HAT_KIND=kind)
    if api_socket:
        child_env['HAT_API_SOCKET'] = api_socket
    args = []
    chosen = selection or {}
    # Model/effort are emitted exactly once; do not repeat the table's convenience args.
    if chosen.get('model'):
        args += ['-m' if kind == 'codex' else '--model', chosen['model']]
    if chosen.get('effort'):
        if kind == 'codex':
            args += ['-c', 'model_reasoning_effort=' + json.dumps(chosen['effort'])]
        elif kind in ('claude', 'agy'):
            args += ['--effort', chosen['effort']]
        elif kind == 'cursor':
            # Cursor accepts model bracket parameters, not a separate --effort flag.
            args[-1] = args[-1] + '[effort=' + chosen['effort'] + ']'
    if sandbox is None:
        args += permissions if permissions is not None else {'codex': ['--yolo'], 'claude': ['--dangerously-skip-permissions'],
                                                          'cursor': ['--yolo', '--trust'], 'agy': ['--dangerously-skip-permissions']}[kind]
    else:
        args += {'codex': ['--sandbox', 'workspace-write', '-a', 'on-request'],
                 'claude': ['--permission-mode', 'default'], 'cursor': ['--sandbox', 'enabled'], 'agy': ['--sandbox']}[kind]
    # Native CLI arguments may contain the first prompt, but policy flags must use wrapper options.
    if any(a.startswith('-') for a in agent_args):
        raise LaunchError('arguments after -- must be the first prompt; use --model/--effort/--sandbox on the wrapper')
    prompt = ' '.join(agent_args)
    if brief:
        path = Path(brief).expanduser().resolve()
        if not path.is_file():
            raise LaunchError(f'brief does not exist: {path}')
        prompt = f'Read and carry out the brief at {path}.\n' + prompt
    prompt = 'Global rules: read ~/.config/agents/AGENTS.md\n' + prompt
    rules = Path(__file__).resolve().parent.parent / 'docs/skills/herdr-coordinator/SKILL.md'
    if role == 'coordinator':
        prompt += f'\nRouting rules: {rules}; spawn workers with herdr-spawn'
    hook_dir = Path(os.environ.get('XDG_RUNTIME_DIR', f'/tmp/herdr-attention-{os.getuid()}')) / 'herdr-attention' / 'launches' / run_id
    hook_command = shlex.join(['/usr/bin/python3', str(Path(__file__).with_name('hook.py'))])
    events = ['SessionStart', 'SessionEnd', 'SubagentStart', 'SubagentStop', 'Stop']
    if kind == 'claude':
        events += ['TaskCompleted']
        args += ['--settings', str(hook_dir / 'claude.json')]
    elif kind == 'codex':
        args += ['--dangerously-bypass-hook-trust']
        for event in events:
            # JSON strings are valid TOML basic strings; build the inline table explicitly.
            value = '[{hooks=[{type="command",command=' + json.dumps(hook_command) + ',timeout=3}]}]'
            args += ['-c', f'hooks.{event}={value}']
    if prompt:
        args += (['--prompt-interactive', prompt] if kind == 'agy' else [prompt])
    return {'name': name, 'title': ' · '.join(v for v in (name, task) if v), 'caller_pane': env['HERDR_PANE_ID'], 'parent_session': parent_identity or _identity(caller), 'split': split,
            'start': ['herdr', 'pane', 'run', '<new pane>'], 'executable': EXECUTABLE[kind], 'args': args,
            'child_env': child_env, 'run_id': run_id, 'hook_dir': str(hook_dir),
            'hook_settings': {'hooks': {e: [{'hooks': [{'type': 'command', 'command': hook_command, 'timeout': 3}]}] for e in events}},
            'tags': {'parent': env['HERDR_PANE_ID'], 'self': '<hash of child session>',
                     **({'hat_parent_self': session_hash(_identity(caller))} if _identity(caller) else {}),
                     **{f'hat_{k}': v[:80] for k, v in (('role', role), ('team', team), ('task', task)) if v}},
            'selection': selection, 'sandbox': sandbox, 'tab': tab, 'placement': placement}


def preview(steps):
    return {'name': steps['name'], 'title': steps['title'], 'caller_pane': steps['caller_pane'], 'split': steps['split'],
            'start': [*steps['start'], shlex.join(['env', *sum((['-u', k] for k in CLEAR_ENV), []),
                      *[f'{k}=<child-only>' for k in steps['child_env']], steps['executable'], *steps['args']])],
            'env_names': sorted(steps['child_env']), 'tags': steps['tags'], 'run_id': steps['run_id'],
            'selection': {k: v for k, v in (steps.get('selection') or {}).items() if k not in ('env', 'args')},
            'placement': steps.get('placement'), 'tab': steps.get('placement', {}).get('label'),
            'note': 'environment values redacted; self is computed after session detection'}


def run(steps: dict, *, name: str, register: Callable[[dict], dict], agent_info: Callable[[str], dict | None],
        role: str | None, team: str | None, task: str | None, reports_to: str | None,
        runner: Callable[[list[str]], str] | None = None, wait_seconds: float = 60, sleep=time.sleep,
        register_launch: Callable | None = None, bind_launch: Callable | None = None) -> dict:
    runner = runner or _run
    name = steps['name']
    caller_identity = steps.get('parent_session') or _identity(agent_info(steps['caller_pane']))
    if reports_to == 'me' and not caller_identity:
        raise LaunchError('--reports-to me needs the caller to be an agent Herdr knows')
    steps['child_env']['HERDR_PARENT_SESSION'] = caller_identity or ''
    if register_launch:
        metadata = {'run_id': steps['run_id'], 'name': name,
                    'kind': steps['executable'].replace('cursor-agent', 'cursor'),
                    'parent_session': caller_identity, 'parent_pane': steps['caller_pane'], 'role': role,
                    'team': team, 'task': task, 'reports_to': caller_identity if reports_to == 'me' else reports_to,
                    'sandbox': steps.get('sandbox')}
        metadata.update({k: (steps.get('selection') or {}).get(k) for k in ('account', 'model', 'effort')})
        register_launch(metadata)
    if steps.get('coordinator_skill'):
        skill = Path(steps['hook_dir']) / 'coordinator/SKILL.md'
        skill.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        skill.write_text(steps['coordinator_skill'])
    if steps['executable'] == 'claude':
        folder = Path(steps['hook_dir'])
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings = folder / 'claude.json'
        settings.write_text(json.dumps(steps['hook_settings']))
        settings.chmod(0o600)
    split = json.loads(runner(steps['split']))
    data = split.get('result') or split
    pane = (data.get('root_pane') or data.get('pane') or {}).get('pane_id')
    if not pane:
        raise LaunchError('Herdr did not return the new pane')
    command = shlex.join(['env', *sum((['-u', k] for k in CLEAR_ENV), []),
                         *[f'{k}={v}' for k, v in steps['child_env'].items()], steps['executable'], *steps['args']])
    try:
        runner([*steps['start'][:3], pane, command])
        identity = None
        named = False
        title = ' · '.join(v for v in (name, task) if v)[:240]
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            info = agent_info(pane)
            identity = _identity(info)
            if info and not named and info.get('agent') == steps['executable'].replace('cursor-agent', 'cursor'):
                runner(['herdr', 'agent', 'rename', pane, name])
                named = True
                if not identity:
                    pending = ['herdr', 'pane', 'report-metadata', pane, '--source', 'herdr-attention', '--title', title]
                    for key, value in (('role', role), ('team', team), ('task', task)):
                        if value:
                            pending += ['--token', f'hat_{key}={value[:80]}']
                    runner(pending)
            if identity:
                break
            sleep(0.5)
        result = {'pane': pane, 'identity': identity, 'run_id': steps['run_id'], 'registered': []}
        if not identity:
            result['warning'] = 'agent started but no session id was detected; nothing was registered. Pane left open for inspection.'
            return result
        if not named:
            runner(['herdr', 'agent', 'rename', pane, name])
        if bind_launch:
            bind_launch({'run_id': steps['run_id'], 'session': identity})
        actions = []
        if caller_identity:
            actions.append({'action': 'link', 'subject': identity, 'kind': 'spawned_by', 'object': caller_identity})
        if reports_to:
            actions.append({'action': 'link', 'subject': identity, 'kind': 'reports_to',
                            'object': caller_identity if reports_to == 'me' else reports_to})
        for kind, value in (('role', role), ('team', team), ('task', task), ('sandbox', steps.get('sandbox'))):
            if value:
                actions.append({'action': 'set', 'subject': identity, 'kind': kind, 'value': value})
        for body in actions:
            register({**body, 'registered': True, 'actor': caller_identity or 'shell'})
            result['registered'].append(body)
        tokens = [f'parent={steps["caller_pane"]}', f'self={session_hash(identity)}']
        if caller_identity:
            tokens.append(f'hat_parent_self={session_hash(caller_identity)}')
        tokens += [f'hat_{k}={v[:80]}' for k, v in (('role', role), ('team', team), ('task', task)) if v]
        mirror = ['herdr', 'pane', 'report-metadata', pane, '--source', 'herdr-attention', '--title', title]
        for token in tokens:
            mirror += ['--token', token]
        runner(mirror)
        result['mirrored_tokens'] = tokens
        return result
    except Exception as exc:
        raise LaunchError(f'launch failed in disposable pane {pane}; pane left open for inspection: {exc}') from exc


def _identity(info: dict | None) -> str | None:
    session = (info or {}).get('agent_session') or {}
    if session.get('value'):
        return f'{session.get("agent") or info.get("agent")}:{session["value"]}'
    return None


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=330)
    if result.returncode != 0:
        # Never echo the pane.run command: it includes environment values and the task.
        raise LaunchError(f'{" ".join(command[:3])} failed (exit {result.returncode})')
    return result.stdout
