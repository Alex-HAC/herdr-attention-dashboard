# How it works

```
Herdr server ──socket (read-only)──▶ collector ──local socket──▶ desktop plugin
                                       │  │                      (bar icon + panel,
                                       │  │                       idle reports)
                                       │  └──HTTP (off by default)──▶ phone page
                                       └──HTTPS──▶ ntfy ──▶ phone (off by default)
```

The Herdr socket is never exposed beyond the collector.

## Parts

| Part | File | Job |
|---|---|---|
| Herdr client | `herdr_attention/herdr.py` | One request per connection; event subscription; refuses write methods |
| Engine | `engine.py` | Observed state per agent, one event per episode, dedupe across restarts |
| Rules | `classify.py` | Turns state into approval / question / blocked / failure / completion / stall / working / idle, with evidence and confidence |
| Store | `store.py` | SQLite: agents, events, settings overrides, phone sessions. No output text |
| Presence | `presence.py` | Idle/active reports → present / idle / away / unknown |
| Notifier | `notify.py` | When to send, message text, ntfy sender |
| API | `api.py` | Local socket (no login, file permissions) and phone server (login, CSRF) |
| Collector | `collector.py` | Threads and wiring |
| CLI | `cli.py` | `herdr-attention ...` |
| Desktop plugin | `plugin/alexvdb.herdr-attention/` | `Service.qml` (data stream, idle), `Panel.qml` + `AgentRow.qml` (UI), `Model.js` (pure helpers) |
| Phone page | `herdr_attention/web/` | Static HTML/JS, polls `/api/view` |

## Flow

1. **Observe.** The watcher subscribes to lifecycle events and to status
   changes of every agent pane in watched workspaces, then asks for a snapshot.
   On a status event it calls `agent.get` for the full record. Lost connection
   → back off and repeat (subscribe, then snapshot).
2. **State.** Each agent is keyed by its Herdr session id. The engine stores
   status, when the collector saw it change (`status_since`, `since_known`),
   Herdr's `state_change_seq`, and for working agents an output hash.
3. **Classify.** `classify.py` decides the kind. `blocked` waits for
   `agent.explain` to say which rule matched before an event is created, so
   there is no "blocked" alert followed by an "approval" alert.
4. **Events.** Entering an alert kind creates one event; leaving it resolves
   the event. Events found only through a snapshot (first start, reconnect)
   are `seen_late`.
5. **Present.** `/api/view` returns watched agents, counts, presence and
   notifier status. The plugin gets it pushed through `/api/stream`
   (`herdr-attention watch`); the phone page polls every 10 s while visible.
6. **Deliver.** Every 5 s the notifier looks at pending events: hold them while
   you are present, send them (one alert, or one summary for several) when you
   count as away. Rate limited, retried with back-off, each event at most once.

## Threads and safety

The worker thread does all Herdr calls except the subscription. The engine,
store and presence objects are locked; API threads only read views or make
small validated changes. A crash in one round is logged and the loop goes on.

## Steering later

Nothing in v1 writes to Herdr. When steering comes, it should be a separate
module with its own allow-list in `herdr.py`, its own API routes behind the
same login/CSRF checks, and a feature flag that the view already reports
(`features.steering`).

## Explain later

`Collector.explain()` is the single place for it. It currently returns
"not set up". A backend should be a small class picked by `explain.backend`,
called only from that route, never from the worker loop.
