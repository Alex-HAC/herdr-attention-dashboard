"""Questions for Alex, using the existing tier/reporting-line classification."""
import re
from .classify import STATE_LABELS
from .content import presentation


def normal(text):
    return re.sub(r'[^a-z0-9]+',' ',text.lower()).strip()


def build_questions(view, settings, now, answered=()):
    by_id={a['identity']:a for a in view['agents']}
    rows=[]
    for agent in view['agents']:
        question=agent.get('question')
        if question and question['id'] not in answered:
            rows.append(dict(question,source_identity=agent['identity']))
        elif question:
            agent.update(question=None,awaiting_user=False,tier=None)
            if agent.get('state')=='needs_you':
                agent['state']='done' if agent.get('status')=='done' else 'waiting'
                agent['state_label']=STATE_LABELS[agent['state']]
            agent['presentation']=presentation(agent,agent.get('summary'),resolved=True)
        coordinator=(agent.get('reports_to') or {}).get('identity')
        evidence=agent.get('tier_evidence') or {}
        quote=evidence.get('question')
        # A coordinator's direct relay wins over an escalated copy of a worker ask.
        if coordinator and quote:
            for existing in rows:
                if existing['identity']==coordinator and normal(existing['text'])==normal(quote):
                    existing['about']=agent['identity']
        timeout=settings.get('attention.coordinator_wait_minutes',0) or 0
        since=agent.get('event_at') or agent.get('status_since') or now
        if timeout and quote and agent.get('tier')=='waiting_on_coordinator' and coordinator in by_id and now-since>=timeout*60:
            q={'id':f"escalated:{agent['identity']}:{since}:{normal(quote)}",'identity':coordinator,
               'source_identity':agent['identity'],'about':agent['identity'],'text':quote,
               'reason':f"Its coordinator has not answered for {int((now-since)/60)} minutes.",
               'at':since,'options':evidence.get('options',[]),'terminal':False}
            if q['id'] not in answered:
                rows.append(q)
    for worker in view['agents']:
        coordinator=(worker.get('reports_to') or {}).get('identity')
        quote=(worker.get('tier_evidence') or {}).get('question')
        for q in rows:
            if quote and q['identity']==coordinator and normal(q['text'])==normal(quote):
                q['about']=worker['identity']
    dedup={}
    for q in rows:
        key=(q['identity'],normal(q['text']))
        if key not in dedup or q['source_identity']==q['identity']:
            previous=dedup.get(key)
            if previous and previous.get('about'):
                q['about']=previous['about']
            dedup[key]=q
    rows=sorted(dedup.values(),key=lambda q:(not q['terminal'],q['at']))
    assigned=set()
    for q in rows:
        if q['identity'] in assigned:
            continue
        assigned.add(q['identity'])
        target=by_id[q['identity']]
        target.update(question=q,awaiting_user=True,tier='waiting_on_you')
        target['reason']=q['reason'];target['snippet']=q['text']
        target['presentation']={'text':q['text'],'kind':'terminal' if q['terminal'] else 'reply','label':'Answer in terminal' if q['terminal'] else 'Reply','target':q['identity'],'question':q,'out_of_date':False}
    return rows
