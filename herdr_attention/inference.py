"""Conservative, passive reporting fallback. Declarations remain authoritative.

Only watched, unambiguous live sessions are read. No agent-control API is used.
Bounded trace/brief text is held in memory; persisted evidence contains paths,
line numbers and identifiers, never conversation excerpts.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .relations import cycles
from .traces import _conversation_item, parse_time, split_identity

SCHEMA = """
CREATE TABLE IF NOT EXISTS inferred_links (
 subject TEXT PRIMARY KEY, object TEXT NOT NULL, since REAL NOT NULL,
 confidence REAL NOT NULL, evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inference_suppressions (
 subject TEXT PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL
);
"""
PATH = re.compile(r"(?:[~/\w.-]+/)*[\w.-]+\.md\b")
COORDINATOR = re.compile(r"^\s*Coordinator:\s*[^\n]*?\b(w\d+:p[\da-fA-F]+)\b", re.I | re.M)


class Inferences:
    def __init__(self, store):
        self.store = store
        with store._lock:
            store._db.executescript(SCHEMA)

    def replace(self, candidates, at):
        with self.store._lock:
            previous = {r[0]: r[1:] for r in self.store._db.execute(
                'SELECT subject, object, since FROM inferred_links')}
            self.store._db.execute('DELETE FROM inferred_links')
            for c in candidates:
                old = previous.get(c['from'])
                since = old[1] if old and old[0] == c['to'] else at
                self.store._db.execute('INSERT INTO inferred_links VALUES (?, ?, ?, ?, ?)',
                                      (c['from'], c['to'], since, c['confidence'], json.dumps(c['evidence'])))

    def suppress(self, subject, at, actor):
        self.store._exec('INSERT OR IGNORE INTO inference_suppressions VALUES (?, ?, ?)', (subject, at, actor))

    def suppressed(self):
        return {r[0] for r in self.store._exec('SELECT subject FROM inference_suppressions').fetchall()}

    def effective(self, state, visible):
        candidates = [dict(zip(('from', 'to', 'since', 'confidence', 'evidence'), r))
                      for r in self.store._exec('SELECT subject, object, since, confidence, evidence FROM inferred_links').fetchall()]
        for c in candidates:
            c['evidence'] = json.loads(c['evidence'])
        return select(candidates, state, visible, self.suppressed())


def select(candidates, state, visible, suppressed=(), ignore_declared=()):
    """Filter dynamically: declaration changes take effect without a trace poll."""
    ignored = set(ignore_declared)
    declared = [(s, o) for s, k, o in state.links if k == 'reports_to' and s not in ignored]
    owners = {s for s, _ in declared}
    out = []
    # Strongest first, stable tie-break; an inferred edge may never close a cycle.
    for c in sorted(candidates, key=lambda c: (-c['confidence'], c['from'], c['to'])):
        s, o = c['from'], c['to']
        if s in owners or s in suppressed or s not in visible or o not in visible or s == o:
            continue
        if (s, o) in cycles(declared + [(s, o)]):
            continue
        out.append({**c, 'kind': 'reports_to', 'provenance': ['inferred'],
                    'inferred': True, 'dashed': True, 'source': 'inferred: ' + '; '.join(c['evidence'])})
        owners.add(s)
        declared.append((s, o))
    return out


class EvidenceReader:
    """Read initial prompt + bounded recent references; never execute trace text."""
    def __init__(self, locator):
        self.locator = locator
        self._cache = {}
        self._lock = threading.Lock()

    def session(self, identity):
        pair = split_identity(identity)
        path = self.locator.find(*pair) if pair else None
        if not path:
            return {}
        try:
            stat = path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            with self._lock:
                cached = self._cache.get(identity)
                if cached and cached[0] == signature:
                    return cached[1]
            with path.open('rb') as f:
                head = f.read(256 * 1024)
                # Truncated JSON lines are ignored; don't join disjoint chunks.
                if stat.st_size > len(head):
                    f.seek(max(len(head), stat.st_size - 512 * 1024))
                    f.readline()
                    tail = f.read(512 * 1024)
                else:
                    tail = b''
        except OSError:
            return {}
        result = {'references': [], 'prompt': '', 'start': None, 'cwd': ''}
        for initial, chunk in ((True, head), (False, tail)):
            for line in chunk.splitlines():
                try:
                    e = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(e, dict):
                    continue
                at = parse_time(e.get('timestamp'))
                if initial and result['start'] is None and at:
                    result['start'] = at
                payload = e.get('payload') if isinstance(e.get('payload'), dict) else {}
                message = e.get('message') if isinstance(e.get('message'), dict) else {}
                if initial and not result['cwd']:
                    cwd = e.get('cwd') or payload.get('cwd', '')
                    result['cwd'] = cwd if isinstance(cwd, str) else ''
                item = _conversation_item(pair[0], e)
                if initial and not result['prompt'] and item and item['role'] == 'user':
                    result['prompt'] = item['text']
                # Exact path references from messages and actual tool inputs only.
                # References are corroboration, never commands to run.
                text = item['text'] if item else ''
                if pair[0] == 'claude' and e.get('type') == 'assistant':
                    blocks = message.get('content')
                    if isinstance(blocks, list):
                        text += json.dumps([b.get('input') for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_use'])
                if e.get('type') == 'response_item' and payload.get('type') == 'function_call':
                    text += str(payload.get('arguments') or '')
                if at:
                    paths = [p for p in PATH.findall(text) if '/briefs/' in p]
                    if paths:
                        result['references'].append((at, paths[:20]))
        with self._lock:
            self._cache[identity] = (signature, result)
        return result

    @staticmethod
    def briefs(session):
        """Follow at most six Markdown references within the named briefs folder.

        Canonical paths must stay inside that folder, including symlinks. Only
        explicitly named files are opened; no directory search or home scan.
        """
        cwd = Path(session.get('cwd') or '/')
        roots = []
        for name in PATH.findall(session.get('prompt', '')):
            p = Path(name).expanduser()
            p = p if p.is_absolute() else cwd / p
            if 'briefs' in p.parts:
                # Preserve the lexical directory so a symlink cannot widen scope.
                root = Path(*p.parts[:p.parts.index('briefs') + 1])
                if root.is_symlink():
                    continue
                roots.append((p, root.resolve()))
        out, seen, todo = [], set(), roots[:6]
        while todo and len(seen) < 6:
            path, root = todo.pop(0)
            try:
                path = path.resolve()
                if path in seen or not path.is_relative_to(root) or path.suffix != '.md' or not path.is_file():
                    continue
                seen.add(path)
                with path.open('rb') as f:
                    raw = f.read(64 * 1024 + 1)
                if len(raw) > 64 * 1024:
                    continue
                text = raw.decode('utf-8')
            except (OSError, UnicodeError, ValueError):
                continue
            out.append((path, text))
            for name in PATH.findall(text):
                p = Path(name).expanduser()
                todo.append((p if p.is_absolute() else path.parent / p, root))
        return out

    def candidates(self, agents, state):
        # A shared native session is never evidence for either live claimant.
        live = {a['identity']: a for a in agents if a.get('present') and not a.get('session_collision')}
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if k in live}
        sessions = {i: self.session(i) for i in live}
        briefs = {i: self.briefs(s) for i, s in sessions.items()}
        parents = {o for s, k, o in state.links if k == 'reports_to'}
        parents.update(i for (i, k), labels in state.labels.items() if k == 'role' and 'coordinator' in labels)
        candidates = []
        for worker, session in sessions.items():
            start = session.get('start')
            if not start or not briefs[worker]:
                continue
            evidence = {}

            def offer(parent, confidence, why, explicit=False):
                if parent == worker or parent not in live or (not explicit and parent not in parents):
                    return
                parent_start = sessions.get(parent, {}).get('start')
                if not parent_start or parent_start > start:
                    return
                confidence_before, reasons = evidence.setdefault(parent, (confidence, []))
                evidence[parent] = (max(confidence, confidence_before), reasons + [why])

            for path, text in briefs[worker]:
                for match in COORDINATOR.finditer(text):
                    for parent, a in live.items():
                        if a.get('pane_id', '').lower() == match[1].lower():
                            line = text.count('\n', 0, match.start(1)) + 1
                            offer(parent, .97, f'first prompt brief {briefs[worker][0][0]}; {path}:{line} names coordinator {a["pane_id"]}', explicit=True)
            # Exact brief reference by a declared coordinator around worker start.
            primary = str(briefs[worker][0][0])
            for parent in parents & live.keys():
                for at, paths in sessions[parent].get('references', []):
                    if start - 1800 <= at <= start + 300 and primary in paths:
                        offer(parent, .90, f'first prompt names {primary}; coordinator trace references this exact brief near worker start')
            # Shared common file + brief directory + nearby declared sibling;
            # directory alone would wrongly capture unrelated older runs.
            worker_paths = {p for p, _ in briefs[worker]}
            for sibling, kind, parent in state.links:
                if kind != 'reports_to' or sibling == worker or sibling not in sessions:
                    continue
                sibling_start = sessions[sibling].get('start')
                shared = worker_paths & {p for p, _ in briefs[sibling]}
                shared = {p for p in shared if 'common' in p.name and p.parent == briefs[worker][0][0].parent}
                if sibling_start and abs(start - sibling_start) <= 12 * 3600 and shared:
                    offer(parent, .85, f'first prompt brief {primary}; declared sibling {live[sibling].get("pane_id")} shares {sorted(map(str, shared))[0]} within 12h')
            # Conflicting evidence must be resolved by a declaration, not guessing.
            if len(evidence) == 1:
                parent, (confidence, reasons) = next(iter(evidence.items()))
                candidates.append({'from': worker, 'to': parent, 'confidence': confidence, 'evidence': reasons[:3]})
        return candidates
