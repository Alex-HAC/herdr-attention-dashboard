"""Current actions and summary freshness, shared by every reading surface."""
from .writing import SECTION_NAMES

SUBAGENT_LABELS = {'finished': 'Finished subagents', 'hide': 'Hide subagents'}
LIVE_COMPLETION_HINT = 'Clears when the pane is viewed in Herdr or the agent moves on.'
# The phone listener cannot focus a pane, so it names the laptop instead.
LIVE_COMPLETION_HINT_REMOTE = 'Clears when you open this pane on the laptop.'


def read_action(agent):
    # A row lifted to Waiting on you by its notice clears with Read, like the notice.
    lifted = bool((agent.get('tier_evidence') or {}).get('notice'))
    live_completion = (agent.get('present') and agent.get('kind') == 'completion' and agent.get('status') == 'done'
                       and not lifted)
    return {'available': bool(agent.get('event_id') and not agent.get('acked') and not live_completion),
            'reason': LIVE_COMPLETION_HINT if live_completion else None,
            'remote_reason': LIVE_COMPLETION_HINT_REMOTE if live_completion else None}


def decorate_subagents(view):
    """Keep history cards, but count and display finished hook children as folds."""
    from .classify import STATE_LABELS
    from .notify_tiers import NOTIFY_TIERS
    cards = {a['identity']: a for a in view['agents']}
    view['subagent_labels'] = SUBAGENT_LABELS
    for card in cards.values():
        card['finished_subagents'] = []
        card['read_action'] = read_action(card)
    for card in cards.values():
        if not card.get('in_process'):
            continue
        finished = not card.get('present') or (card.get('status') != 'working' and
                    (card.get('acked') or card.get('status') in ('done', 'closed', 'gone', 'finished', 'completion')))
        parent = cards.get(card.get('in_process_parent'))
        seen = {card['identity']}
        while parent and parent.get('in_process') and parent['identity'] not in seen:
            seen.add(parent['identity'])
            parent = cards.get(parent.get('in_process_parent'))
        card['folded'] = bool(finished and parent and parent['identity'] not in seen)
        if card['folded']:
            card['fold_parent'] = parent['identity']
            parent['finished_subagents'].append(card['identity'])
    for card in cards.values():
        card['finished_subagents_label'] = (f"{SUBAGENT_LABELS['finished']} · {len(card['finished_subagents'])}"
                                             if card['finished_subagents'] else None)
    visible = [a for a in cards.values() if not a.get('folded')]
    for workspace in view['workspaces']:
        members = [a for a in visible if a['workspace_id'] == workspace['id']]
        workspace['agent_count'] = len(members)
        workspace['folded_subagent_count'] = sum(a.get('folded', False) for a in cards.values() if a['workspace_id'] == workspace['id'])
        for tab in workspace.get('tabs', []):
            tab['agents'] = [a['identity'] for a in members if a.get('tab_id') == tab['id']]
        for state in STATE_LABELS:
            workspace['counts'][state] = sum(a.get('state') == state for a in members)
        for tier in NOTIFY_TIERS:
            workspace['counts'][tier] = sum(a.get('tier') == tier for a in members)
        workspace['attention'] = sum(bool(a.get('tier')) for a in members)
    for state in STATE_LABELS:
        view['counts']['state_' + state] = sum(a.get('state') == state for a in visible)
    for tier in NOTIFY_TIERS:
        view['counts'][tier] = sum(a.get('tier') == tier for a in visible)
    view['counts']['attention'] = sum(bool(a.get('tier')) for a in visible)


def agent_name(agent, event=None):
    """Status titles are not names. Use task metadata, then the pane label."""
    from .classify import LABELS, STATE_LABELS
    event = event or {}
    labels = {str(v).casefold() for v in (*LABELS.values(), *STATE_LABELS.values())}
    for value in (agent.get('name'), agent.get('title'), event.get('title')):
        if value and value.strip().casefold() not in labels:
            return value.strip()
    return 'Agent ' + str(event.get('pane_id') or agent.get('pane_id') or 'unknown')


def short_name(agent, event=None):
    """agent_name() for one-line texts: a task title is cut at " | "."""
    name = agent_name(agent, event)
    return name.split(' | ')[0].strip() or name


def event_label(event):
    from .classify import LABELS
    if event.get('notify_state') == 'expired':
        return 'No longer needed'
    return LABELS.get(event['type'], event['type'])


def outdated(row, agent):
    if not row or row.get('status') != 'complete':
        return False
    ended = row.get('finished_at') or row.get('at') or row.get('requested_at') or 0
    source = row.get('source_to') or ended
    if (agent.get('last_assistant') or {}).get('at', 0) and (agent['last_assistant']['at'] or 0) > source + 1:
        return True
    if (agent.get('last_activity_at') or 0) > ended + 1:
        return True
    if row.get('event_id') is not None and row.get('event_id') != agent.get('event_id'):
        return True
    needs = (row.get('summary') or row).get('needs_you') or {}
    return bool('question' in agent and needs.get('kind') in ('approval','question','decision') and not agent.get('question'))


def presentation(agent, row=None, *, resolved=False):
    old = outdated(row, agent)
    question = agent.get('question')
    handled = bool(agent.get('acked') or resolved or ((agent.get('last_assistant') or {}).get('last_role') == 'user'))
    if question and not handled:
        return {'out_of_date': old, 'text': question['text'],
                'label': 'Answer in terminal' if question['terminal'] else 'Reply',
                'kind': 'terminal' if question['terminal'] else 'reply', 'target': question['identity'],
                'question': question}
    if handled:
        return {'out_of_date': old, 'text': 'Already handled', 'kind':'none'}
    if agent.get('kind') == 'quota_stop':
        return {'out_of_date': old, 'text':'Wait for the account reset, then resume the agent.', 'kind':'open_agent', 'label':'Open agent', 'target':agent.get('identity')}
    if agent.get('kind') in ('failure','stall','blocked') and not agent.get('acked'):
        return {'out_of_date':old,'text':'Check the latest output and decide whether to restart.',
                'kind':'open_agent','label':'Open agent','target':agent.get('identity')}
    if agent.get('tier') == 'waiting_on_you':
        return {'out_of_date':old,'text':'Open the agent and decide.','kind':'open_agent','label':'Open agent','target':agent.get('identity')}
    return {'out_of_date':old,'text':'Nothing to do — for your information','kind':'none'}
