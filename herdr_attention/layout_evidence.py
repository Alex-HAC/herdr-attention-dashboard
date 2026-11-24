"""Bounded, read-only report/PR verification, away from the collector worker.

Only a DONE report in the watched cwd or /tmp is read. A GitHub pull URL is
queried using gh; text/AI claims of merging are never accepted as evidence.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import threading
import time

from .sanitize import strip_terminal

PR = re.compile(r'https://github\.com/[\w.-]+/[\w.-]+/pull/\d+\b')
DONE = re.compile(r'^\s*DONE\s+["\']?(.+?\.(?:md|txt))["\']?\s*$', re.M)


def candidate(text, cwd):
    text = strip_terminal(text)
    reports = list(DONE.finditer(text))
    if not reports or not cwd:
        return None
    raw = Path(reports[-1][1]).expanduser()
    raw = raw if raw.is_absolute() else Path(cwd) / raw
    blocked = [Path.home() / '.codex-personal', Path.home() / '.claude-personal']
    if any(raw.is_relative_to(p) or Path(cwd).is_relative_to(p) for p in blocked):
        return None
    path, root = raw.resolve(), Path(cwd).expanduser().resolve()
    if any(path.is_relative_to(p) for p in blocked):
        return None
    if not (path.is_relative_to(root) or path.is_relative_to(Path('/tmp').resolve())) or not path.is_file():
        return None
    with path.open('r', errors='replace') as stream:
        urls = set(PR.findall(stream.read(65536)))
    if not urls:
        urls = set(PR.findall(text))
    return {'report': str(path), 'pr': next(iter(urls))} if len(urls) == 1 else None



class CompletionEvidence:
    def __init__(self, clock=time.time, runner=subprocess.run):
        self.clock, self.runner = clock, runner
        self._lock = threading.Lock()
        self.pending, self.verified, self.checked = {}, {}, {}
        self.wake = threading.Event()

    def consider(self, identity, text, cwd):
        try:
            found = candidate(text, cwd)
        except (OSError, ValueError):
            return
        if found:
            with self._lock:
                if self.pending.get(identity) != found:
                    self.pending[identity] = found
                    self.verified.pop(identity, None)
                    self.checked.pop(identity, None)
                if len(self.pending) > 500:
                    old = next(iter(self.pending))
                    self.pending.pop(old, None)
                    self.verified.pop(old, None)
                    self.checked.pop(old, None)
            self.wake.set()

    def resolve(self, allowed):
        with self._lock:
            for identity in set(self.pending) - set(allowed):
                self.pending.pop(identity, None)
                self.verified.pop(identity, None)
                self.checked.pop(identity, None)
            pending = [(i, dict(c)) for i, c in self.pending.items()
                       if self.clock() - self.checked.get(i, -1e12) >= 300 and i not in self.verified][:4]
        for identity, found in pending:
            proof = None
            try:
                result = self.runner(['gh', 'pr', 'view', found['pr'], '--json', 'state,mergedAt'],
                                     capture_output=True, text=True, timeout=5, check=False)
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    if data.get('state') == 'MERGED' and data.get('mergedAt') and Path(found['report']).is_file():
                        proof = {**found, 'merged_at': data['mergedAt']}
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass  # Missing/unavailable gh is unknown, never evidence of merging.
            with self._lock:
                if self.pending.get(identity) == found and identity in allowed:
                    self.checked[identity] = self.clock()
                    if proof:
                        self.verified[identity] = proof

    def facts(self):
        with self._lock:
            return {i: dict(v) for i, v in self.verified.items() if Path(v['report']).is_file()}
