"""Narrow focus capability. Never accepts keys, prompts, or command text."""
from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess

from .desktop import raise_herdr
from .herdr import HerdrClient
from .ui_contract import hash_path


class HerdrFocus(HerdrClient):
    # Reuse the transport, without changing the read client's allowlist.
    ALLOWED_METHODS = frozenset({'workspace.focus', 'tab.focus', 'pane.focus'})


@dataclass(frozen=True)
class FocusTarget:
    identity: str
    pane_id: str
    name: str | None
    tab_id: str | None
    workspace_id: str | None

    @classmethod
    def resolve(cls, engine, identity: str) -> 'FocusTarget':
        agent = engine.agent_by_identity(identity)
        if not agent or not agent.get('present') or not agent.get('pane_id'):
            raise LookupError('no live focus target')
        return cls(identity, agent['pane_id'], agent.get('name'), agent.get('tab_id'), agent.get('workspace_id'))

    def fallback_steps(self):
        for kind in ('workspace', 'tab', 'pane'):
            value = getattr(self, kind + '_id')
            if value:
                yield kind + '.focus', {kind + '_id': value}


class FocusService:
    def __init__(self, engine, socket_path: str):
        self.engine = engine
        self.socket_path = socket_path

    def focus(self, identity: str) -> dict:
        target = FocusTarget.resolve(self.engine, identity)
        try:
            # Pane IDs are unique even when a name has been recycled. Always
            # target the resolved pane and preserve this collector's socket.
            env = dict(os.environ, HERDR_SOCKET_PATH=self.socket_path)
            subprocess.run(['herdr', 'agent', 'focus', target.pane_id], env=env,
                           capture_output=True, text=True, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            client = HerdrFocus(self.socket_path)
            for method, params in target.fallback_steps():
                client.call(method, params)
        try:
            window = raise_herdr()
        except (OSError, ValueError, subprocess.SubprocessError):
            raise RuntimeError('could not raise Herdr window') from None
        return {'ok': True, 'identity': identity, 'hash': hash_path('conversation', identity), 'window': window}
