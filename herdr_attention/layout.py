"""Read-only layout planning; only the launcher creates the resulting pane/tab."""
from __future__ import annotations
import re


def generated_name(policy, *, role, provider, task, suffix):
    role = policy.get('role_abbrev', {}).get(role, role)
    value = policy.get('pane_name_template', '{role}-{provider}-{task}').format(
        role=role, provider=provider, task=task or suffix)
    value = re.sub('[^a-z0-9_-]+', '-', value.lower()).strip('-_')
    if not value or not value[0].isalpha():
        value = 'wrk-' + value
    return value[:min(32, policy.get('pane_name_max_len', 32))].rstrip('-_')


def choose(policy, *, name, role, provider, workspace, caller_pane, caller_tab,
           tabs, panes, new_tab=False, explicit_split=False):
    """Use an eligible group tab, or overflow to a meaningful new tab label.

    Explicit pane names are preserved. Splitting a different group tab changes
    layout only; the caller remains the child's creation parent.
    """
    if not policy:
        return {'create_tab': new_tab, 'label': name, 'target': caller_pane, 'why': 'explicit/default topology'}
    limit = policy.get('max_panes_per_tab', 4)
    tabs = [t for t in tabs if t.get('workspace_id') == workspace]
    panes = [p for p in panes if p.get('workspace_id') == workspace]
    labels = {t.get('label', '') for t in tabs}
    if role == 'coordinator' and policy.get('coordinator_own_tab', True):
        label = policy.get('coordinator_tab_template', '{name} coordinator').format(name=name)
        base, number = label, 2
        while label in labels:
            label, number = f'{base}-{number}', number + 1
        return {'create_tab': True, 'label': label, 'target': caller_pane, 'why': 'coordinator owns a tab'}
    group = role if policy.get('group_workers_by') == 'role' else provider
    base = policy.get('worker_tab_template', '{group}').format(group=group)
    if policy.get('forbid_numeric_tab_labels', True) and base.isnumeric():
        base = provider
    if explicit_split and not new_tab:
        members = [p for p in panes if p.get('tab_id') == caller_tab]
        if len(members) < limit:
            return {'create_tab': False, 'label': None, 'target': caller_pane, 'why': 'explicit split below pane cap'}
    if not new_tab and not explicit_split:
        candidates = sorted((t for t in tabs if t.get('label') == base or
                             re.fullmatch(re.escape(base) + r'-\d+', t.get('label', ''))),
                            key=lambda t: (t.get('label') != base, t.get('label', '')))
        for tab in candidates:
            members = [p for p in panes if p.get('tab_id') == tab['tab_id']]
            if members and len(members) < limit:
                return {'create_tab': False, 'label': tab['label'], 'target': members[-1]['pane_id'],
                        'why': f'{group} group tab below {limit}-pane cap'}
    label, number = base, 2
    while label in labels:
        label, number = f'{base}-{number}', number + 1
    return {'create_tab': True, 'label': label, 'target': caller_pane, 'why': 'new/overflow group tab'}


def unique_name(value, occupied=(), max_len=32):
    """Repair invalid names and keep deterministic, bounded numeric suffixes."""
    import unicodedata
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower()
    value = re.sub('[^a-z0-9_-]+', '-', value).strip('-_')
    if not value or not value[0].isalpha():
        value = 'wrk-' + value
    value = value[:max_len].rstrip('-_')
    candidate, number = value, 2
    while candidate in occupied:
        suffix = f'-{number}'
        candidate = value[:max_len - len(suffix)].rstrip('-_') + suffix
        number += 1
    return candidate


def findings(agents, tabs, panes, policy, *, cards=(), completions=None):
    """Quiet diagnostics from a watched snapshot. Commands are text, never run."""
    import shlex
    from .engine import identity_of
    by_pane = {c.get('pane_id'): c for c in cards if c.get('pane_id')}
    occupied = {a.get('name') for a in agents if a.get('name')}
    tab_labels = {t.get('label') for t in tabs}
    result = []
    def add(kind, message, *, pane=None, tab=None, commands=(), evidence=None):
        scope = pane or tab
        result.append({'kind': kind, 'message': message, 'workspace_id': scope.get('workspace_id'),
                       'tab_id': scope.get('tab_id'), 'pane_id': pane.get('pane_id') if pane else None,
                       'commands': [shlex.join(c) for c in commands], 'evidence': evidence})
    for tab in tabs:
        members = [p for p in panes if p.get('tab_id') == tab['tab_id']]
        cap = policy.get('max_panes_per_tab', 4)
        if len(members) > cap:
            add('over_limit', f"Tab {tab.get('label') or tab['tab_id']!r} has {len(members)} panes (limit {cap}); use an overflow tab.", tab=tab)
        label = (tab.get('label') or '').strip()
        if not label or label.isnumeric() or re.fullmatch(r'(?:tab|terminal|untitled|new tab)(?:\s*\d+)?', label, re.I):
            providers = {a.get('agent') for a in agents if a.get('tab_id') == tab['tab_id'] and a.get('agent')}
            group = next(iter(providers)) if len(providers) == 1 else 'workers'
            proposed, number = group, 2
            while proposed in tab_labels:
                proposed, number = f'{group}-{number}', number + 1
            tab_labels.add(proposed)
            add('default_tab_label', f"Tab {label or '(unnamed)'!r} needs a descriptive label.", tab=tab,
                commands=[['herdr', 'tab', 'rename', tab['tab_id'], proposed]])
    for agent in agents:
        if not agent.get('agent'):
            continue
        card = by_pane.get(agent['pane_id'], {})
        roles = card.get('roles') or {}
        role = next((v['value'] for v in roles.get('role', [])), 'worker')
        task = next((v['value'] for v in roles.get('task', [])), '')
        proposed = agent.get('name')
        if not proposed:
            proposed = unique_name(generated_name(policy, role=role, provider=agent['agent'], task=task or 'task', suffix='task'), occupied)
            occupied.add(proposed)
            add('unnamed_agent', f"Agent pane {agent['pane_id']} has no name.", pane=agent,
                commands=[['herdr', 'agent', 'rename', agent['pane_id'], proposed]])
        title = (agent.get('terminal_title_stripped') or agent.get('terminal_title') or '').strip()
        if not title or re.match(r'^(?:continue\b|new (?:task|session|chat)\b|untitled\b|ask .+ to do anything)', title, re.I):
            desired = proposed + (f' · {task}' if task else '')
            add('generic_title', f"Pane {agent['pane_id']} has a generic task title: {title or '(empty)'!r}.", pane=agent,
                commands=[['herdr', 'pane', 'report-metadata', agent['pane_id'], '--source', 'herdr-attention', '--title', desired]])
        proof = (completions or {}).get(card.get('identity') or identity_of(agent))
        if proof and agent.get('agent_status') in ('done', 'idle'):
            add('finished_pane', f"Pane {agent['pane_id']}: report exists and PR is merged; consider closing after review.", pane=agent,
                commands=[['herdr', 'pane', 'close', agent['pane_id']]], evidence=proof)
    return result
