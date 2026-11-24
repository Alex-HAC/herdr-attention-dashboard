"""Herdr 0.9.1 sidebar projection: display-only API, collector DB remains authoritative.

Inspired by Algorant/herdr-agent-tree (MIT). We do not modify its tokens or
view. A foreign view owner makes this projection passive until disabled/re-enabled.
"""
from __future__ import annotations

from .herdr import HerdrClient, READ_METHODS

SOURCE = 'plugin:herdr-attention'


def ordering_key(card):
    """Herdr position only; missing positions fall back to display numbers and IDs."""
    positions = []
    for axis in ('workspace', 'tab', 'pane'):
        value = next((card.get(f'{axis}_{field}') for field in ('order', 'number')
                      if type(card.get(f'{axis}_{field}')) is int), float('inf'))
        positions.append(value)
    return (*positions, card.get('pane_id') or '', card.get('identity') or '')


class ProjectionClient(HerdrClient):
    # This separate client cannot start, prompt, focus or interrupt an agent.
    ALLOWED_METHODS = READ_METHODS | {'agent.view.set', 'agent.view.clear', 'pane.report_metadata'}


def placements(view):
    """A deterministic forest. Ambiguous/cyclic parentage remains unindented."""
    cards = {a['identity']: a for a in view['agents'] if a.get('pane_id') and a.get('present')}
    parents = {}
    for link in view['links']:
        child, parent = link['from'], link['to']
        if link['kind'] == 'spawned_by' and child in cards and parent in cards and child != parent:
            parents.setdefault(child, set()).add(parent)
    unique = {c: next(iter(p)) for c, p in parents.items() if len(p) == 1}
    from .relations import cycles
    cyclic = cycles(list(unique.items()))
    unique = {c: p for c, p in unique.items() if (c, p) not in cyclic}
    children = {}
    for child, parent in unique.items():
        children.setdefault(parent, []).append(child)
    ordered = sorted(cards, key=lambda identity: ordering_key(cards[identity]))
    order = {identity: index for index, identity in enumerate(ordered)}
    out = []
    def walk(identity, depth):
        out.append((cards[identity], depth))
        for child in sorted(children.get(identity, []), key=order.get):
            walk(child, depth + 1)
    for root in ordered:
        if root not in unique:
            walk(root, 0)
    return out


class Sidebar:
    def __init__(self, socket_path=None, client=None):
        self.client = client or ProjectionClient(socket_path)
        self.last = {}
        self.owned = False
        self.passive = False
        self.disabled = False

    def clear(self):
        self.client.call('agent.view.clear', {'source': SOURCE})
        # TTL handles closed/replaced panes; never write to a new occupant on cleanup.
        self.last.clear()
        self.owned = False

    def update(self, view, *, enabled=True):
        if not enabled:
            if not self.disabled:
                self.clear()
            self.disabled = True
            self.passive = False
            return
        if self.disabled:
            self.disabled = False
        if self.passive:
            return
        if callable(view):   # v0.9.8.7: the view is built only when it will be drawn
            view = view()
        probe = self.client.call('agent.view.clear', {'source': SOURCE + '-probe'})
        if probe.get('active') and probe.get('source') != SOURCE:
            self.passive = True
            return
        if 'active' not in probe:
            self.passive = True
            return
        self.owned = probe.get('active', False) and probe.get('source') == SOURCE
        if not self.owned:
            response = self.client.call('agent.view.set', {'source': SOURCE, 'label': 'tree', 'sort': [
                {'field': {'token': 'hat_tree_rank'}, 'order': 'asc'},
                {'field': 'workspace_order', 'order': 'asc'}, {'field': 'tab_order', 'order': 'asc'},
                {'field': 'pane_order', 'order': 'asc'}]})
            self.owned = response.get('active', False) and response.get('source') == SOURCE
            if not self.owned:
                return

        for rank, (card, depth) in enumerate(placements(view)):
            # Check the live occupant immediately before writing display hints.
            info = self.client.agent(card['pane_id'])
            from .launcher import _identity
            if (_identity(info) != (card.get('session_identity') or card['identity'])
                    or (card.get('terminal_id') and (info or {}).get('terminal_id') != card['terminal_id'])):
                continue
            roles = card.get('roles', {}).get('role', [])
            role = roles[0]['value'] if roles else ''
            branch = ('  ' * min(depth, 12) + '└─ ' if depth else '') + (f'[{role}] ' if role else '')
            summary = card.get('finished_subagents_label')
            display = branch + card['agent']
            if summary:
                display = display[:max(0, 117 - len(summary))] + ' · ' + summary
            # TTL removes a decoration after a session changes or the collector stops.
            self.client.call('pane.report_metadata', {'pane_id': card['pane_id'], 'source': 'herdr-attention-sidebar',
                             'tokens': {'hat_tree_rank': f'{rank:06d}', 'hat_tree_branch': branch or None},
                             'display_agent': display[:120], 'ttl_ms': 10000})
