# Discovery failure: evidence and repair (8 November 2026)

## Proven cause

The installed Herdr 0.9.1/protocol 22 schema (`herdr api schema --json`)
distinguishes two envelopes:

- Generic lifecycle `schemas.event`: `event` is snake_case, including
  `pane_created`, `pane_updated`, `pane_agent_detected`, `pane_closed`, etc.
  Creation/update/move data nest a `pane` (PaneInfo).
- Per-pane status `schemas.subscription_event`: `event` is dotted,
  `pane.agent_status_changed`, with top-level pane/workspace ids.
- Subscription request types are dotted for both families.

The old collector subscribed with the correct dotted request types but
compared all incoming event names against dotted strings. It therefore
silently ignored the generic lifecycle family. It also lacked create/update
branches and had no periodic reconciliation. Only previously known panes
received per-pane status subscriptions, so newly detected agents remained
absent until startup/reconnect performed another list snapshot.

Bounded wire verification used just one disposable ordinary shell, w7:p6,
created from this worker's w7:p5 with `--no-focus --cwd /tmp`, then closed in
`finally`. No agent/model was launched and no existing pane was controlled.
The actual messages were:

```json
{"event":"pane_created","data_type":"pane_created","data_keys":["pane","type"],"pane_id":"w7:p6"}
{"event":"pane_updated","data_type":"pane_updated","data_keys":["pane","type"],"pane_id":"w7:p6"}
```

Only names, keys and pane id were retained, in
`/tmp/dash-discovery-event-proof.json`. No terminal contents were captured.
The installed schema requires top-level pane_id/workspace_id for
pane_agent_detected: the earlier suggestion that these fields are normally
missing is **not supported**. No recording of the four original launches
exists, so their individual historical event sequences cannot be recovered.
The live wire/schema mismatch proves the broken lifecycle path independently.

## Repair

Normalize both envelope families. Treat topology messages as hints, coalesced
at ingress into a single pending flag. One worker performs authoritative
agent.list/workspace.list/tab.list reconciliation at most once per two seconds
under event load and every 45 seconds when quiet. Missed/incomplete/out-of-order
messages cannot permanently suppress discovery or remove a replacement session
using an old close event. List reads have the existing five-second socket
limit; discovery never reads output/transcripts or invokes models.

On failure, retain the previous snapshot and retry after 5/10/20/40/80/160/180
seconds (capped); event storms cannot bypass backoff. Failure logging follows
that retry schedule. Incomplete-pane hints log at most once per minute.
Successful scans update last_reconcile and clear reconcile_error/failures;
doctor and browser show discovery freshness/staleness (90-second threshold),
independently of stream connection status. Pane subscriptions are refreshed
when the watched pane set changes, including removals.

Reuse keeps session identity; late session identification migrates existing
history/read state. Snapshot ordering cannot retire a session moved to another
pane. Shell records without an identified agent are excluded. Disabled
workspaces never reach explain, trace targets, or output reads through discovery.
Existing graph layout/viewport code and notification/summary dedupe are reused.
A small null-evidence decoding guard covers the separately reported history
failure; its fixture uses a nullable projected legacy row.

## Verification

Fixture tests in tests/test_discovery.py cover dropped/incomplete/out-of-order
and both-schema events, topology storms, periodic discovery without restart,
new watch/disable privacy, session identification, move/reassignment/reuse,
removal/shell exclusion, disconnect/startup, acknowledgement retention and retry
backoff. Existing browser gesture tests cover an agent arriving/leaving during
updates without changing zoom or established positions. Live metadata proof
above is separate from fixtures; no paid live test was run.
