"""Persistent quota-stop episodes, from the provider's own limit banner (linking.quota_banner)."""
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .linking import quota_banner, quota_signal
from .quotas import account_matches
from .classify import STATE_LABELS

SCHEMA = """CREATE TABLE IF NOT EXISTS agent_quota_stops (
 identity TEXT PRIMARY KEY, provider TEXT NOT NULL, account_id TEXT NOT NULL, account_label TEXT NOT NULL,
 at REAL NOT NULL, reset_at REAL, screen_hash TEXT NOT NULL, event_id INTEGER, acked_at REAL,
 active INTEGER NOT NULL, reset_seen INTEGER NOT NULL DEFAULT 0
);"""
FIELDS = ('identity','provider','account_id','account_label','at','reset_at','screen_hash','event_id','acked_at','active','reset_seen')

# "resets Nov 23, 8pm (Europe/Amsterdam)", "reset at 8pm", "resets Mon 20:00"
_RESET = re.compile(
    r"resets?\s+(?:on\s+)?(?:at\s+)?"
    r"(?:(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?P<day>\d{1,2}),?\s*)?"
    r"(?:(?P<wd>Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+)?"
    r"(?:at\s+)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?"
    r"(?:\s*\((?P<zone>[^)]+)\))?",
    re.I)
_MONTHS = {n: i for i, n in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def parse_reset(text: str, now: float, default_zone: str = "Europe/Amsterdam") -> float | None:
    """The reset time written on a provider banner, or None."""
    if not text:
        return None
    m = _RESET.search(text.replace("\u2019", "'"))
    if not m or not m.group("hour"):
        return None
    zone = (m.group("zone") or default_zone).strip()
    try:
        tz = ZoneInfo(zone)
    except Exception:
        tz = ZoneInfo(default_zone)
    local = datetime.fromtimestamp(now, tz)
    hour, minute = int(m.group("hour")), int(m.group("minute") or 0)
    ampm = (m.group("ampm") or "").lower().replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    year, month, day = local.year, local.month, local.day
    if m.group("month") and m.group("day"):
        month, day = _MONTHS[m.group("month")[:3].lower()], int(m.group("day"))
        if month < local.month or (month == local.month and day < local.day):
            # In late December a January reset belongs to next year.
            if local.month == 12 and month <= 2:
                year += 1
    try:
        target = local.replace(year=year, month=month, day=day, hour=hour, minute=minute,
                               second=0, microsecond=0)
    except ValueError:
        return None
    if target.timestamp() < now - 14 * 86400:
        return None
    return target.timestamp()


def window_full(window: dict) -> bool:
    try:
        if window.get("used_pct") is not None:
            return float(window["used_pct"]) >= 100
        return float(window.get("percent") or 0) >= 0.999
    except (TypeError, ValueError):
        return False


def window_reset(window: dict) -> float | None:
    raw = window.get("resets_at")
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def match_account(agent: dict, accounts: list, banner: str, now: float):
    """The account this banner belongs to: launch/proof first, else a 100% window whose reset matches the banner."""
    direct = next((a for a in accounts if account_matches(a, agent)), None)
    parsed = parse_reset(banner, now)

    def full_windows(account):
        return [w for w in (account.get("windows") or []) if window_full(w)]

    if direct:
        full = full_windows(direct)
        if not full and (direct.get("windows") or []):
            return None, None  # matched account is not actually at a limit
        reset_at = None
        if parsed:
            close = [window_reset(w) for w in full if window_reset(w) and abs(window_reset(w) - parsed) < 3600]
            reset_at = min(close) if close else parsed
        if reset_at is None:
            resets = [window_reset(w) for w in full if window_reset(w)]
            reset_at = min(resets) if resets else None
        return direct, reset_at
    if parsed:
        hits = []
        for account in accounts:
            for w in full_windows(account):
                at = window_reset(w)
                if at and abs(at - parsed) < 3600:
                    hits.append((account, at))
        if hits:
            hits.sort(key=lambda h: abs(h[1] - parsed))
            return hits[0]
    return None, parsed


def limit_is_real(agent: dict, accounts: list, banner: str, now: float) -> bool:
    """A genuine banner is enough when we have no account data. If we do, a window must actually be at 100%."""
    if not accounts:
        return True
    account, _ = match_account(agent, accounts, banner, now)
    if account is not None:
        return True
    provider = (agent.get("identity") or "").split(":", 1)[0]
    related = [a for a in accounts if str(a.get("provider") or a.get("id") or "").startswith(provider)]
    if not related:
        return True
    return any(window_full(w) for a in related for w in (a.get("windows") or []))


def infer_reset(store, identity: str, now: float) -> float | None:
    """The reset mentioned on a recent quota card, when the stop row has none."""
    rows = store._exec(
        "SELECT e.title, n.title, n.body FROM events e "
        "LEFT JOIN notification_text n ON n.event_id=e.id "
        "WHERE e.identity=? AND e.at>=? ORDER BY e.at DESC LIMIT 20",
        (identity, now - 21 * 86400),
    ).fetchall()
    for title, ntitle, body in rows:
        for text in (body, ntitle, title):
            at = parse_reset(text or "", now)
            if at:
                return at
    return None


def already_seen(store, identity: str, reset_at: float | None, now: float) -> bool:
    """Alex already got a card for this agent and this limit/reset (sent, listed, or acked)."""
    since = (reset_at - 21 * 86400) if reset_at else now - 21 * 86400
    until = reset_at if reset_at else now + 1
    rows = store._exec(
        "SELECT e.type, e.title, e.acked_at, e.notify_state, e.at, n.title "
        "FROM events e LEFT JOIN notification_text n ON n.event_id = e.id "
        "WHERE e.identity=? AND e.at>=? AND e.at<?",
        (identity, since, until),
    ).fetchall()
    shown = {"sent", "summarized", "dashboard", "acked", "gated"}
    for typ, title, acked, state, at, ntitle in rows:
        if typ == "quota_stop" or quota_signal(title or "") or quota_signal(ntitle or ""):
            if acked or state in shown:
                return True
    return False


class QuotaStops:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock
        store._exec(SCHEMA)

    def get(self, identity):
        row = self.store._exec('SELECT '+','.join(FIELDS)+' FROM agent_quota_stops WHERE identity=?',(identity,)).fetchone()
        return dict(zip(FIELDS,row)) if row else None

    def clear(self, identity, *, forget_screen: bool = False):
        """End a live stop. The screen hash is kept (v0.9.8.10) so the same banner and reset
        are not raised again; pass forget_screen=True only for the one-time false-positive cleanup."""
        old = self.get(identity)
        if not old:
            return
        hash_ = '' if forget_screen else (old['screen_hash'] or '')
        self.store._exec(
            "UPDATE agent_quota_stops SET active=0, screen_hash=? WHERE identity=?",
            (hash_, identity))
        if forget_screen and old['event_id']:
            ev = self.store.event(old['event_id']) if hasattr(self.store, 'event') else None
            if ev and ev.get('resolved_at') is None:
                self.store.resolve_event(old['event_id'], self.clock())

    def end(self, identity, *, banner_gone: bool = False):
        """Deactivate without forgetting the screen, so a later flicker of the same banner is not a new card."""
        old = self.get(identity)
        if not old:
            return
        if old['active']:
            self.store._exec('UPDATE agent_quota_stops SET active=0 WHERE identity=?', (identity,))
        if banner_gone and old['event_id']:
            ev = self.store.event(old['event_id']) if hasattr(self.store, 'event') else None
            if ev and ev.get('resolved_at') is None:
                self.store.resolve_event(old['event_id'], self.clock())

    def _write(self, identity, provider, account_id, label, at, reset_at, digest, event_id, acked, active, reset_seen):
        self.store._exec('INSERT OR REPLACE INTO agent_quota_stops VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (identity, provider, account_id, label, at, reset_at, digest, event_id, acked, int(active), int(reset_seen)))

    def observe(self, agent, text, accounts):
        identity = agent['identity']
        old = self.get(identity)
        now = self.clock()
        digest = hashlib.sha256(text.encode()).hexdigest()
        working = agent.get('status') == 'working'
        banner = quota_banner(text)
        # A working agent with the banner still on screen is almost always a Herdr flicker
        # (this coordinator sat on the same limit banner while status flipped done/working
        # all day). Deactivate the live flag but keep the episode; do not forget the screen.
        if working:
            self.end(identity, banner_gone=not banner)
            return
        if not banner:
            self.end(identity, banner_gone=True)
            return
        if not limit_is_real(agent, accounts or [], text, now):
            self.end(identity, banner_gone=False)
            return
        account, reset_at = match_account(agent, accounts or [], text, now)
        provider = identity.split(':', 1)[0]
        account_id = account['id'] if account else provider + ':unknown'
        if account:
            prefix = (account.get('provider_name') or account.get('name') or '').strip()
            label = (prefix + ' ' + account['label']).strip() if prefix and prefix.lower() not in account['label'].lower() else account['label']
        else:
            label = provider + ' (account not recorded)'
        if reset_at is None and old:
            reset_at = old['reset_at']
        # A new cycle only when the previous episode started before its reset and that reset
        # has now passed, or the account published a later reset window.
        reset_passed = bool(old and old['reset_at'] and now >= old['reset_at'] and old['at'] < old['reset_at'])
        if reset_at and old and old['reset_at'] and reset_at > old['reset_at'] + 3600:
            reset_passed = True

        same = old and ((old['screen_hash'] and old['screen_hash'] == digest) or (
            old['reset_at'] and reset_at and abs(old['reset_at'] - reset_at) < 3600 and not reset_passed))
        if old and old['active'] and not reset_passed:
            if (account or reset_at) and (old['account_id'].endswith(':unknown') or not old['reset_at']):
                keep_id = account_id if old['account_id'].endswith(':unknown') and account else old['account_id']
                keep_label = label if account and old['account_id'].endswith(':unknown') else old['account_label']
                self._write(identity, provider, keep_id, keep_label, old['at'],
                            reset_at or old['reset_at'], digest, old['event_id'],
                            old['acked_at'], 1, old['reset_seen'])
            return
        if same and not reset_passed:
            keep_id = old['account_id'] if old and not old['account_id'].endswith(':unknown') else account_id
            keep_label = old['account_label'] if old and not old['account_id'].endswith(':unknown') else label
            self._write(identity, provider, keep_id, keep_label, old['at'] if old else now,
                        reset_at or (old['reset_at'] if old else None), digest,
                        old['event_id'] if old else None, old['acked_at'] if old else None,
                        0 if (old and old['acked_at']) else 1, old['reset_seen'] if old else 0)
            return
        if already_seen(self.store, identity, reset_at, now) and not reset_passed:
            # Completions had been AI-rewritten into "Weekly limit reached" and acked; do not start again.
            self._write(identity, provider, account_id, label, old['at'] if old else now,
                        reset_at, digest, old['event_id'] if old else None,
                        old['acked_at'] if old else None, 0, 0)
            return
        if old and old['event_id'] and reset_passed:
            ev = self.store.event(old['event_id']) if hasattr(self.store, 'event') else None
            if ev and ev.get('resolved_at') is None:
                self.store.resolve_event(old['event_id'], now)
        note = label + ' out of quota' + (' · reset passed; it can be checked for resuming' if reset_passed else '')
        # Quota cards never push the phone (Alex's standing rule); record them as dashboard-only.
        event = self.store.add_event(
            at=now, identity=identity, workspace_id=agent.get('workspace_id'), pane_id=agent.get('pane_id'),
            type='quota_stop', evidence={'notify_tier': 'might_look', 'note': note, 'account': account_id},
            confidence='high', seen_late=False, notify_state='dashboard', title='Out of quota')
        self._write(identity, provider, account_id, label, now, reset_at, digest, event, None, 1, int(reset_passed))

    def holds_completion(self, identity: str) -> bool:
        """True when a new "Finished" for this agent would just be the same limit card again."""
        now = self.clock()
        old = self.get(identity)
        reset_at = (old['reset_at'] if old else None) or infer_reset(self.store, identity, now)
        if reset_at and now >= reset_at and not (old and old['active']):
            return False
        if old and old['active']:
            return True
        if old and old['screen_hash'] and (not reset_at or now < reset_at):
            return True
        if already_seen(self.store, identity, reset_at, now) and (not reset_at or now < reset_at):
            return True
        return False

    def active(self):
        return [r[0] for r in self.store._exec('SELECT identity FROM agent_quota_stops WHERE active=1').fetchall()]

    def prune(self, present):
        """End the stops of agents that are gone. Returns the identities cleared."""
        gone = [i for i in self.active() if i not in present]
        for identity in gone:
            self.end(identity, banner_gone=False)
        return gone

    def ack(self, identity, event_id=None):
        stop = self.get(identity)
        if stop and (event_id is None or stop['event_id'] == event_id):
            self.store._exec('UPDATE agent_quota_stops SET acked_at=? WHERE identity=?', (self.clock(), identity))
            if stop['event_id']:
                self.store.ack_event(stop['event_id'], self.clock())

    def decorate(self, view, enabled=True):
        groups={}
        for a in view['agents']:
            stop=self.get(a['identity'])
            if not stop or not stop['active']:
                continue
            a['quota_stop']=stop;a['quota_wall']=stop
            a.update(state='out_of_quota',state_label='Out of quota',kind='quota_stop',label='Out of quota',
                event_id=stop['event_id'],event_at=stop['at'],acked=bool(stop['acked_at']),question=None,awaiting_user=False,
                tier=None if stop['acked_at'] else 'might_look',tier_label='Might want a look',
                reason=stop['account_label']+' out of quota',snippet=None)
            a['presentation']={'text':'Wait for the account reset, then resume the agent.','kind':'open_agent','label':'Open agent','target':a['identity'],'out_of_date':False}
            if stop['acked_at']:
                continue
            group=groups.setdefault(stop['account_id'],{'account_id':stop['account_id'],'label':stop['account_label'], 'reset_at':stop['reset_at'],'agents':[]})
            group['agents'].append({'identity':a['identity'],'name':a.get('name') or a.get('pane_id'),'event_id':stop['event_id']})
        for group in groups.values():
            count=len(group['agents'])
            reset=datetime.fromtimestamp(group['reset_at'],ZoneInfo('Europe/Amsterdam')).strftime('%a %H:%M') if group['reset_at'] else None
            group['summary']=f"{count} agent{'s' if count!=1 else ''} stopped: {group['label']} out of quota" + (f", resets {reset}" if reset else ", reset time unknown")
        view['quota_stops']={'enabled':enabled,'label':STATE_LABELS['out_of_quota'],'groups':list(groups.values())}
        view['questions']=[q for q in view.get('questions',[]) if not self.get(q['identity']) or not self.get(q['identity'])['active']]
        for workspace in view['workspaces']:
            members=[a for a in view['agents'] if a['workspace_id']==workspace['id']]
            for state in ('needs_you','working','done','waiting','parked','unknown','out_of_quota'):
                workspace['counts'][state]=sum(a['state']==state for a in members)
            workspace['attention']=sum(bool(a.get('tier')) for a in members)
            workspace['counts']['waiting_on_you']=sum(a.get('tier')=='waiting_on_you' for a in members)
        for state in ('needs_you','working','done','waiting','parked','unknown','out_of_quota'):
            view['counts']['state_'+state]=sum(a['state']==state for a in view['agents'])
        for tier in ('waiting_on_you','might_look','fyi_finished','waiting_on_coordinator'):
            view['counts'][tier]=sum(a.get('tier')==tier for a in view['agents'])
