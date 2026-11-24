"""Per-launch metadata-only hook. No transcript, prompt or auth material leaves stdin."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import subprocess

# Executable by absolute path from both installed and checkout launches.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from herdr_attention.ui_contract import API
from herdr_attention.cli import Client
from herdr_attention.herdr import HerdrClient


def payload(raw, env):
    kind = env.get('HAT_KIND')
    session = raw.get('session_id') or raw.get('thread-id')
    event = raw.get('hook_event_name') or ('Stop' if raw.get('type') == 'agent-turn-complete' else None)
    if kind not in ('claude', 'codex') or not session or not env.get('HAT_RUN_ID'):
        return None
    body = {'session': f'{kind}:{session}', 'parent_session': env.get('HERDR_PARENT_SESSION') or None,
            'parent_pane': env.get('HERDR_PARENT_PANE'), 'run_id': env['HAT_RUN_ID'], 'event': event}
    if event in ('SubagentStart', 'SubagentStop') and raw.get('agent_id'):
        # Claude's short task ids are scoped to the parent session; Codex uses thread UUIDs.
        child = raw['agent_id'] if kind == 'codex' else f'{session}/subagent/{raw["agent_id"]}'
        body['child_session'] = f'{kind}:{child}'
    return body


def report_session(body, raw, env):
    """Publish only this launch's native session id, never lifecycle/input control.

    Herdr 0.9.1 resolves session reports through the provider integration source.
    Subagent events and a replaced native session must not relabel the pane.
    """
    pane, kind = env.get('HERDR_PANE_ID'), env.get('HAT_KIND')
    if body['event'] not in ('SessionStart', 'Stop') or raw.get('agent_id') or not pane:
        return
    info = HerdrClient().agent(pane)
    session = (info or {}).get('agent_session') or {}
    if not info or info.get('agent') != kind:
        return
    if session.get('value') and body['session'] != f"{session.get('agent') or kind}:{session['value']}":
        return
    subprocess.run(['herdr', 'pane', 'report-agent-session', pane, '--source', f'herdr:{kind}',
                    '--agent', kind, '--agent-session-id', body['session'].split(':', 1)[1]],
                   capture_output=True, timeout=2, check=False)


def main():
    try:
        raw = json.loads(sys.stdin.buffer.read(65537))
        body = payload(raw, os.environ)
        if body and os.environ.get('HAT_API_SOCKET'):
            try:
                report_session(body, raw, os.environ)
            except Exception:
                pass
            Client(os.environ['HAT_API_SOCKET']).request('POST', API['hooks'], body, timeout=2)
    except Exception:
        # Observation cannot block, continue, or restart an agent.
        pass
    print('{}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
