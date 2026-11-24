"""Resolve the recipient on the server; a worker is never a fallback recipient."""
from .content import short_name


def _index(view):
    agents = {a['identity']: a for a in view['agents']}
    parents = {}
    for l in view.get('links', []):
        if l.get('kind') == 'reports_to':
            parents.setdefault(l.get('from'), l.get('to'))   # first link wins, as before
    return agents, parents


def recipient(view, identity, index=None):
    agents, parents = index or _index(view)
    current = agents.get(identity)
    seen = set()
    while current:
        if current['identity'] in seen:
            raise ValueError('Reporting line has a cycle. Refresh before replying.')
        seen.add(current['identity'])
        parent = parents.get(current['identity'])
        parent = parent or current.get('in_process_parent')
        if not parent:
            if not current.get('present') or not current.get('pane_id') or current.get('session_collision'):
                raise ValueError('The recipient is unavailable. Nothing was sent.')
            return current
        current = agents.get(parent)
    raise ValueError('The coordinator is outside the watched agents. Nothing was sent.')


def target_info(agent):
    return {key: agent.get(key) for key in ('identity', 'workspace_label', 'tab_label', 'status')} | {'name': short_name(agent)}


LEGACY = ('id', 'identity', 'agent_name', 'status', 'sent_at', 'error')


def last_reply(agent, target, rows, answers):
    """v0.9.6 contract: Alex's own last reply from this card ({"text", "at", "to"}), only
    when it is newer than the agent's current message, else None.

    Rows are the replies, newest first. A reply belongs to the card it was sent from
    (replies.about); older rows without it count for the card whose recipient got them.
    The 0.9.5 keys (id, identity, agent_name, status, sent_at, error: the "Sent to ... at"
    receipt) stay on the same object for 0.9.5 clients only."""
    since = (agent.get('last_assistant') or {}).get('at') or 0
    # v0.9.8.10: a reply belongs to the episode it answered. A later "Finished" or quota
    # card (event_at / last_done) must not keep "You replied Sure to Finished, not reviewed".
    for extra in (agent.get('event_at'), (agent.get('last_done') or {}).get('at')):
        if extra:
            since = max(since, float(extra))
    labels = None
    for row in rows:
        mine = row['about'] == agent['identity'] if row['about'] else bool(target) and row['identity'] == target['identity']
        if not mine:
            continue
        if row['at'] <= since:
            return None
        to = row['agent_name']
        if to:
            if labels is None:
                from .classify import LABELS, STATE_LABELS
                labels = {str(v).casefold() for v in (*LABELS.values(), *STATE_LABELS.values())}
            if str(to).strip().casefold() in labels:
                to = None
        return {'text': answers.get(row['id'], row['text']), 'at': row['at'], 'to': to,
                **{key: row[key] for key in LEGACY}}
    return None


def decorate(view, replies):
    keys = ('id', 'identity', 'agent_name', 'text', 'at', 'status', 'sent_at', 'error', 'about')
    rows = [dict(zip(keys, row)) for row in replies.store._exec(
        f"SELECT {', '.join(keys)} FROM replies WHERE status IN ('sent', 'queued', 'sending') ORDER BY id DESC LIMIT 500").fetchall()]
    # Answers to question items: show Alex's own words, not the "Answer to ..." wrapper.
    answers = dict(replies.store._exec('SELECT reply_id, text FROM ask_answers WHERE reply_id IS NOT NULL').fetchall())
    index = _index(view)
    for agent in view['agents']:
        target = None
        try:
            target = recipient(view, agent['identity'], index)
            agent['reply_target'] = target_info(target)
        except ValueError as exc:
            agent['reply_target'] = {'error': str(exc)}
        agent['last_reply'] = last_reply(agent, target, rows, answers)
