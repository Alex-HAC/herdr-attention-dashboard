"""Session-bound launch observations and quota walls, stored in the collector DB."""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from .relations import RelationError
from .sanitize import strip_terminal

IDENTITY = re.compile(r'^(claude|codex|cursor|agy|term|shell):[^\s\x00-\x1f]{1,240}$')
EVENTS = ('SessionStart', 'SessionEnd', 'SubagentStart', 'SubagentStop', 'Stop', 'TaskCompleted')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS launches (
 run_id TEXT PRIMARY KEY, at REAL NOT NULL, data TEXT NOT NULL, session TEXT
);
CREATE TABLE IF NOT EXISTS hook_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, run_id TEXT NOT NULL,
 session TEXT NOT NULL, event TEXT NOT NULL, child TEXT
);
CREATE TABLE IF NOT EXISTS hook_agents (
 identity TEXT PRIMARY KEY, parent TEXT NOT NULL, run_id TEXT NOT NULL,
 status TEXT NOT NULL, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quota_walls (
 provider TEXT NOT NULL, premium_only INTEGER NOT NULL, at REAL NOT NULL,
 until_at REAL, identity TEXT NOT NULL, PRIMARY KEY(provider, premium_only)
);
'''


def session_hash(identity: str) -> str:
    """Scheme inspired by Algorant/herdr-agent-tree (MIT); hash the session, not the pane."""
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def valid_identity(value) -> bool:
    return isinstance(value, str) and bool(IDENTITY.fullmatch(value))


def validated_parent(info: dict, agents: list[dict]) -> str | None:
    from .launcher import _identity
    identity = _identity(info)
    tokens = info.get('tokens') or {}
    if identity and sum(_identity(a) == identity for a in agents) > 1:
        return None
    if not identity or tokens.get('self') != session_hash(identity):
        return None
    matches = [a for a in agents if a.get('pane_id') == tokens.get('parent')]
    if len(matches) != 1:
        return None
    parent = _identity(matches[0])
    if parent and sum(_identity(a) == parent for a in agents) > 1:
        return None
    if not parent or parent == identity or tokens.get('hat_parent_self') != session_hash(parent):
        return None
    return parent


# The providers' own limit messages. Not the words "usage", "quota" or "limit" on their own: agents
# that build quota features write those all day (0.9.6.2: nearly every agent read "Out of quota").
_LIMIT_KIND = r"(?:usage|5-hour|five-hour|session|weekly|monthly|daily|opus|sonnet|rate|premium request)"
_QUOTA_PHRASE = (
    r"you(?:[’']ve| have) (?:hit|reached|exceeded) (?:your|the) [a-z0-9 -]{0,40}limit"
    r"|you(?:[’']re| are) out of (?:extra usage|credits|quota)"
    r"|(?:claude |codex |cursor )?(?:" + _LIMIT_KIND + r" ){1,2}limit (?:reached|exceeded)"
    r"|limit reached\s*[∙·•|-]\s*resets"
    r"|(?:insufficient|no remaining|out of) credits|credit balance is too low"
    r"|(?:model|server) is at capacity|too many requests"
    r"|usage_limit_reached|rate_limit_exceeded")
_QUOTA_ANYWHERE = re.compile(_QUOTA_PHRASE, re.I)
_TITLE_ONLY = re.compile(r"^\s*out of quota\b|\b(?:is|are|ran|run|went) out of quota\b|"
                         r"\b(?:blocked|stopped|paused|halted) (?:by|on|at|due to) (?:the |a |an |its )?"
                         r"(?:(?:usage|rate|session|weekly|5-hour) limit|quota)\b", re.I)
# On a screen the message must open its line as a banner does: after nothing but the tool's own
# marks (never a list, diff, quote, table, heading or reply bullet), and it must end there or go
# on like a banner ("... resets 3pm", "... Upgrade to Pro"), not like a sentence about it.
_BANNER_START = r"^[\s⎿■▲⚠✗✖×!]{0,6}"
_BANNER_END = r"(?=(?<=resets)\s|\s*(?:$|[.,:;!∙·•(—]|resets?\b|your\b|try\b|upgrade\b|until\b|please\b|wait\b|for\b))"
_NOT_BARE = r"(?!usage_limit_reached|rate_limit_exceeded)"       # bare tokens are code, not a banner
_QUOTA_LINE = re.compile(_BANNER_START + r"(?:api error:? ?)?" + _NOT_BARE + r"(?:" + _QUOTA_PHRASE + r")" + _BANNER_END, re.I)
# An API error line: a 429 or 529 status, or the raw error JSON.
_API_LINE = re.compile(r"^[\s⎿■▲⚠✗✖×!]{0,6}(?:(?:api )?error:? ?\(?(?:429|529)\b[^\n]{0,200}|"
                       r"\{\s*\"type\"\s*:\s*\"error\"[^\n]{0,200}(?:rate_limit_error|usage_limit_reached|rate_limit_exceeded))", re.I)
_RULE_LINE = re.compile(r"^[\s─━═╌┄\-_=│┃|╭╮╰╯┌┐└┘]+$")
BANNER_LINES = 20


def quota_signal(text: str) -> bool:
    """A provider's limit message in a short text (an alert title). For a screen use quota_banner."""
    text = strip_terminal(text)
    return bool(_QUOTA_ANYWHERE.search(text) or _TITLE_ONLY.search(text))


def quota_banner(text: str) -> bool:
    """The provider's own limit banner in the live bottom of a screen (its last 20 text lines).

    Prose, code, diffs and tool output further up never count, and neither does a line that only
    mentions the message inside a sentence, a list, a quote or a table. A banner wrapped over two
    lines by a narrow pane is read as one."""
    lines = [line.rstrip() for line in strip_terminal(text).splitlines() if line.strip() and not _RULE_LINE.match(line)]
    lines = lines[-BANNER_LINES:]
    for i, line in enumerate(lines):
        joined = line + " " + lines[i + 1].strip() if i + 1 < len(lines) else line
        for candidate in (line, joined):
            if len(candidate) <= 480 and (_QUOTA_LINE.match(candidate) or _API_LINE.match(candidate)):
                return True
    return False


class Observations:
    def __init__(self, store, relations, clock):
        self.store, self.relations, self.clock = store, relations, clock
        with store._lock:
            store._db.executescript(SCHEMA)

    def launch(self, body: dict) -> dict:
        try:
            run_id = str(uuid.UUID(body.get('run_id', '')))
        except (ValueError, TypeError, AttributeError):
            raise RelationError('run_id must be a UUID') from None
        parent = body.get('parent_session') or None
        if parent and not valid_identity(parent):
            raise RelationError('invalid parent_session identity')
        if body.get('kind') not in ('claude', 'codex', 'cursor', 'agy', 'shell'):
            raise RelationError('invalid launch kind')
        # Whitelist metadata: no arbitrary command, account environment, prompt, or hook payload is stored.
        keys = ('name', 'kind', 'role', 'team', 'task', 'sandbox', 'account', 'model', 'effort',
                'parent_session', 'parent_pane', 'reports_to')
        data = {k: body[k] for k in keys if body.get(k) is not None}
        if any(not isinstance(v, str) or len(v) > 512 or any(ord(c) < 32 for c in v) for v in data.values()):
            raise RelationError('launch labels must be printable text, at most 512 characters')
        for key in ('role', 'team', 'task', 'sandbox'):
            if data.get(key) and len(data[key]) > 80:
                raise RelationError(f'{key} must be at most 80 characters')
        existing = self.get_launch(run_id)
        if existing:
            if existing != data:
                raise RelationError('run_id already registered with different metadata')
            return {'ok': True, 'run_id': run_id}
        self.store._exec('INSERT INTO launches VALUES (?, ?, ?, NULL)',
                         (run_id, self.clock(), json.dumps(data)))
        return {'ok': True, 'run_id': run_id}

    def get_launch(self, run_id):
        row = self.store._exec('SELECT data FROM launches WHERE run_id=?', (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def bind(self, run_id, identity):
        data = self.get_launch(run_id)
        if not data or not valid_identity(identity):
            raise RelationError('unknown run or invalid session identity')
        row = self.store._exec('SELECT session FROM launches WHERE run_id=?', (run_id,)).fetchone()
        if row[0] and row[0] != identity:
            raise RelationError('run already bound to a different session')
        self.store._exec('UPDATE launches SET session=? WHERE run_id=?', (identity, run_id))
        parent = data.get('parent_session')
        meta = dict(at=self.clock(), provenance='registered', actor=parent or 'shell')
        if parent and parent != identity:
            self.relations.link(identity, 'spawned_by', parent, **meta)
        for key in ('role', 'team', 'task', 'sandbox'):
            if data.get(key):
                self.relations.set_label(identity, key, data[key], **meta)
        target = data.get('reports_to')
        if target:
            self.relations.link(identity, 'reports_to', target, **meta)
        return data

    def hook(self, body):
        run_id, identity, event = body.get('run_id'), body.get('session'), body.get('event')
        data = self.get_launch(run_id)
        if not data or event not in EVENTS or not valid_identity(identity):
            raise RelationError('unknown launch, event, or invalid session')
        if body.get('parent_session') != data.get('parent_session') or body.get('parent_pane') != data.get('parent_pane'):
            raise RelationError('hook parent does not match registered launch')
        if not identity.startswith(data['kind'] + ':'):
            raise RelationError('hook kind does not match launch')
        child = body.get('child_session')
        if event in ('SubagentStart', 'SubagentStop') and (
                not valid_identity(child) or child == identity or not child.startswith(data['kind'] + ':')):
            raise RelationError('subagent event needs a distinct child_session')
        virtual = self.store._exec('SELECT run_id FROM hook_agents WHERE identity=?', (identity,)).fetchone()
        if not virtual or virtual[0] != run_id:
            self.bind(run_id, identity)
        if event in ('SubagentStart', 'SubagentStop'):
            if not valid_identity(child) or child == identity or not child.startswith(data['kind'] + ':'):
                raise RelationError('subagent event needs a distinct child_session')
            self.relations.link(child, 'spawned_by', identity, at=self.clock(),
                                provenance='observed-spawn', actor=identity)
            self.store._exec('INSERT INTO hook_agents VALUES (?, ?, ?, ?, ?) ON CONFLICT(identity) DO UPDATE SET '
                             'status=excluded.status, at=excluded.at',
                             (child, identity, run_id, 'working' if event == 'SubagentStart' else 'done', self.clock()))
        self.store._exec('INSERT INTO hook_events (at, run_id, session, event, child) VALUES (?, ?, ?, ?, ?)',
                         (self.clock(), run_id, identity, event, child))
        return {'ok': True}

    def card(self, identity):
        row = self.store._exec('SELECT run_id, data FROM launches WHERE session=? ORDER BY at DESC LIMIT 1',
                              (identity,)).fetchone()
        if not row:
            return None
        data = json.loads(row[1])
        # Parent/reporting endpoints can be unwatched; public cards expose no hidden ids.
        for key in ('parent_session', 'parent_pane', 'reports_to'):
            data.pop(key, None)
        return {'run_id': row[0], **data}

    def walls(self):
        rows = self.store._exec('SELECT provider, premium_only, at, until_at, identity FROM quota_walls '
                                'WHERE until_at IS NULL OR until_at>?', (self.clock(),)).fetchall()
        return [dict(zip(('provider', 'premium_only', 'at', 'until', 'identity'), r)) for r in rows]

    def wall(self, provider, identity, *, premium_only=False, timeout=3600):
        if provider not in ('claude', 'codex', 'cursor', 'agy'):
            return
        now = self.clock()
        # Repeated polling of the same screen must not extend the expiry indefinitely.
        active = any(w['provider'] == provider and bool(w['premium_only']) == premium_only for w in self.walls())
        if not active:
            self.store._exec('INSERT OR REPLACE INTO quota_walls VALUES (?, ?, ?, ?, ?)',
                             (provider, int(premium_only), now, now + timeout if timeout else None, identity))

    def clear_walls_of(self, identity):
        """Drop the walls this agent's screen raised (it is working again, or the banner is gone)."""
        self.store._exec('DELETE FROM quota_walls WHERE identity=?', (identity,))

    def clear_wall(self, provider):
        if provider not in ('claude', 'codex', 'cursor', 'agy'):
            raise RelationError('unknown provider')
        self.store._exec('DELETE FROM quota_walls WHERE provider=?', (provider,))
        return {'ok': True}
