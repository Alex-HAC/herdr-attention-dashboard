"""A realistic dashboard data set, produced by the real engine from fake agents.

Fixture-based (no Herdr needed). Times are relative to the real clock so the
page's "5m ago" texts make sense. Contains every state, tabs, a timeline,
finished work, trace metrics, a cross-workspace spawn link, a link to an
agent in an unwatched workspace (must show as hidden), an unwatched
workspace and a hostile task title.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone

import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from herdr_attention.collector import Collector
from tests.helpers import Clock, FakeHerdr, agent, settings
from tests.trace_fixtures import claude_trace, codex_trace, codex_usage, sid

EVIL_TITLE = '<img src=x onerror="document.title=\'pwned\'">'
HIDDEN_LABEL = "Private side project"
SECRET_SESSION = sid("secret")
FAKE_SUMMARY = {
    "headline": "Cart page scaffolded; wants to run npm test",
    "accomplished": ["Scaffolded the cart page"],
    "needs_you": {"kind": "approval", "ask": "Run npm test in ./cart?",
                  "options": ["1. Yes", "2. Yes, and don't ask again for npm test", "4. Wipe the repo"],
                  "why": "Running commands needs your approval"},
    "tests": [], "claims": [{"text": "Pushed to main", "evidence": "pushed to main"}],
    "next_step": None,
}

# v0.9.6 question items (docs/briefs/V0.9.6-ITEMS-CONTRACT.md): real-shaped messages; the
# server (asks.py) turns them into agent.asks.
# docs-lead reports to Alex with three numbered questions and one decision; release-notes
# reports with nothing to answer; import-check may want something but asks nobody.
DOCS_LEAD_REPORT = """Docs v2 is drafted and the site builds cleanly (41 pages, 0 broken links). Nothing is published yet.

**What changed**
- A new getting-started page and a rewritten API guide.
- The old FAQ is folded into the guide.

**Questions for Alex**
1. Should I publish the docs site to the public URL today?
2. Which style do you want for code samples: A) short snippets or B) full runnable files?
3. What name should the landing page use for the product?

**Decision for Alex:** the search box needs a paid plan, so I left it out. Adding it costs 20 euros a month; say if you want it.

I will wait for your answers before I publish anything."""
RELEASE_NOTES_REPORT = """Release notes for 2.3 are written and saved as `docs/releases/2.3.md`.

- 14 changes, grouped as Features, Fixes and Docs.
- The links to the 9 merged pull requests are checked.

Nothing else is open on my side."""
IMPORT_CHECK_REPORT = """The customer import finished: 1,204 rows loaded and 3 rows skipped because the email field was empty.

Could the skipped rows be test data? They are listed in `import-skipped.csv`."""



# The quota fixture files carry real reset times from the day they were recorded.
# The page reads them with the real clock, so copy them and move every reset that
# was still ahead on that day by the time that has passed since: a window that was
# open then is open now, and one that had expired stays expired.
_QUOTA_RECORDED = datetime(2026, 11, 15, 11, 21, tzinfo=timezone.utc)


def _fresh_quota_usage(root: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "quotas" / "usage"
    target = root / "quota-usage"
    target.mkdir()
    passed = datetime.now(timezone.utc) - _QUOTA_RECORDED

    def move(match):
        try:
            reset = datetime.fromisoformat(match.group(1))
        except ValueError:
            return match.group(0)
        if reset.tzinfo is None or reset <= _QUOTA_RECORDED:
            return match.group(0)
        return f'"resetsAt": "{(reset + passed).isoformat()}"'

    for path in source.iterdir():
        if path.suffix == ".json":
            (target / path.name).write_text(re.sub(r'"resetsAt": "([^"]+)"', move, path.read_text()))
        elif path.is_file():
            (target / path.name).write_bytes(path.read_bytes())
    return target

def _write_ask_traces(root: Path, now: float) -> None:
    claude_trace(root, sid("docslead"), now - 40 * 60, [
        {"user": "Draft docs v2 and tell me what you need from me", "reply": [DOCS_LEAD_REPORT]},
    ], model="claude-opus-5-5", effort="high")
    claude_trace(root, sid("notes"), now - 35 * 60, [
        {"user": "Write the 2.3 release notes", "reply": [RELEASE_NOTES_REPORT]},
    ], model="claude-sonnet-5-5")
    claude_trace(root, sid("import"), now - 30 * 60, [
        {"user": "Run the customer import", "reply": [IMPORT_CHECK_REPORT]},
    ], model="claude-sonnet-5-5")


def _write_traces(root: Path, now: float) -> None:
    claude_trace(root, sid("lead"), now - 5 * 3600, [
        {"user": "Plan the checkout redesign", "reply": ["Here is the plan."],
         "usage": {"input_tokens": 40, "cache_creation_input_tokens": 9000, "cache_read_input_tokens": 120000, "output_tokens": 3000}},
        {"compact": True, "user": "Continue", "reply": ["Continuing after compaction."], "retry": True,
         "usage": {"input_tokens": 20, "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 30000, "output_tokens": 800}},
        {"user": "Review it", "reply": ["Reviewed: two open questions.", "Second block of the same reply."],
         "usage": {"input_tokens": 10, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 41000, "output_tokens": 600}},
    ], model="claude-opus-5-5", effort="xhigh")
    claude_trace(root, sid("docs"), now - 26 * 3600, [
        {"user": "Write the API guide", "reply": ["Done."],
         "usage": {"input_tokens": 5, "cache_creation_input_tokens": 500, "cache_read_input_tokens": 8000, "output_tokens": 400}},
    ], model="claude-sonnet-5-5")   # an older Claude Code: no effort in the trace
    codex_trace(root, sid("ui"), now - 3 * 3600,
                [{"total": codex_usage(300000, 250000, 9000, 2000), "last": codex_usage(90000, 80000, 300)},
                 {"total": codex_usage(520000, 470000, 15000, 3000), "last": codex_usage(150000, 140000, 400)}],
                messages=[("user", "Build the cart page"), ("assistant", "Cart page scaffolded; need approval to run tests.")],
                model="gpt-6.2-sol", effort="high")
    codex_trace(root, sid("api"), now - 2 * 3600,
                [{"total": codex_usage(80000, 60000, 2000), "last": codex_usage(40000, 30000, 200)}], parent=sid("ui"),
                model="gpt-6.2-sol", effort="medium")
    codex_trace(root, sid("qa"), now - 90 * 60,
                [{"total": codex_usage(50000, 40000, 1500), "last": codex_usage(30000, 25000, 100)}], parent=sid("ui"),
                messages=[("assistant", "Which date range should the QA run cover?")], model="gpt-6.2-sol", effort="low")
    codex_trace(root, sid("etl"), now - 30 * 3600,
                [{"total": codex_usage(900000, 850000, 30000, 8000), "last": codex_usage(380000, 370000, 500), "window": 475000}],
                fork=SECRET_SESSION, model="gpt-5.1-codex-max", effort="xhigh")
    # The unwatched agent's trace exists but must never be read.
    codex_trace(root, SECRET_SESSION, now - 50 * 3600,
                [{"total": codex_usage(999999, 1, 1), "last": codex_usage(1, 1, 1)}],
                messages=[("user", "SECRET TASK")])


def build(watched=("w1", "w2", "w3"), asks: bool = False) -> dict:
    """asks=True adds the v0.9.6 question-item agents (docs-lead, release-notes, import-check)
    to the Docs workspace, with docs-lead's first question answered; without it the data set
    is the same as before."""
    now = time.time()
    clock = Clock(now - 3 * 3600)
    ws = [
        {"workspace_id": "w1", "label": "Website", "number": 1},
        {"workspace_id": "w2", "label": "Data pipeline", "number": 2},
        {"workspace_id": "w3", "label": "Docs", "number": 3},
        {"workspace_id": "w9", "label": HIDDEN_LABEL, "number": 9},
    ]
    agents = [
        agent("w1:p1", "working", session=sid("lead"), name="web-lead", title="Plan the checkout redesign", kind="claude"),
        agent("w1:p2", "working", session=sid("ui"), name="ui-builder", title="Build the cart page", kind="codex"),
        agent("w1:p3", "working", session=sid("api"), name="api-worker", title=EVIL_TITLE, kind="codex"),
        agent("w1:p4", "idle", session=sid("old"), name="old-spike", title="Spike: payment SDK", kind="claude"),
        agent("w2:p1", "working", session=sid("etl"), name="etl", title="Backfill March data", kind="codex"),
        agent("w2:p2", "working", session=sid("qa"), name=None, title="", kind="codex"),
        agent("w3:p1", "idle", session=sid("docs"), name="docs-writer", title="Write the API guide", kind="claude"),
        agent("w9:p1", "blocked", session=SECRET_SESSION, name="secret-agent", title="SECRET TASK", kind="codex"),
    ]
    if asks:
        agents += [
            agent("w3:p2", "working", session=sid("docslead"), name="docs-lead", title="Docs v2", kind="claude"),
            agent("w3:p3", "working", session=sid("notes"), name="release-notes", title="Release notes 2.3", kind="claude"),
            agent("w3:p4", "working", session=sid("import"), name="import-check", title="Customer import", kind="claude"),
        ]
    tabs = {"w1:p1": "w1:tA", "w1:p2": "w1:tB", "w1:p3": "w1:tB", "w1:p4": "w1:tC",
            "w2:p1": "w2:tA", "w2:p2": "w2:tA", "w3:p1": "w3:tA", "w3:p2": "w3:tB", "w3:p3": "w3:tB",
            "w3:p4": "w3:tB", "w9:p1": "w9:tA"}
    for info in agents:
        info["tab_id"] = tabs[info["pane_id"]]
    herdr = FakeHerdr(agents, ws)
    tab_labels = {"w1:tA": "Lead", "w1:tB": "Builders", "w1:tC": "Spikes", "w2:tA": "Pipeline", "w3:tA": "Docs", "w9:tA": "x"}
    if asks:
        tab_labels["w3:tB"] = "Docs v2"
    herdr.tabs = lambda: [{"tab_id": t, "workspace_id": t.split(":")[0], "label": label, "number": i + 1}
                          for i, (t, label) in enumerate(tab_labels.items())]
    herdr.explain_rules = {"w1:p2": "bash_permission_prompt", "w2:p2": "mcp_elicitation_prompt"}
    herdr.outputs["w1:p1"] = "planning...\n\x1b[31mtoken=abc123\x1b[0m\nstep 2 of 5"
    herdr.screens["w1:p2"] = ("╭──────────────────────────╮\n│ Run npm test in ./cart?  │\n│ ❯ 1. Yes                 │\n"
                              "│   2. Yes, and don't ask again for npm test │\n│   3. No, and tell Codex what to do │\n"
                              "╰──────────────────────────╯\n esc to cancel")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_traces(root, now)
        if asks:
            _write_ask_traces(root, now)
        collector = Collector(db_path=":memory:", client=herdr,
                              settings=settings(watch={"enabled_workspaces": list(watched)},
                                                stall={"minutes": 15, "output_poll_seconds": 60},
                                                traces={"claude_dirs": [str(root / "claude")],
                                                        "codex_dirs": [str(root / "codex")]},
                                                quotas={"usage_dir": str(_fresh_quota_usage(root)),
                                                        "order_path": str(Path(__file__).parent / "fixtures" / "quotas" / "order.json"),
                                                        "timezone": "Europe/Amsterdam"}),
                              clock=clock)
        lead, ui, qa, docs = (f"claude:{sid('lead')}", f"codex:{sid('ui')}", f"codex:{sid('qa')}", f"claude:{sid('docs')}")

        def change(pane, **fields):
            current = herdr.agent(pane)
            current.update(fields)
            current["state_change_seq"] = current["state_change_seq"] + 1
            herdr.set(current)
            collector.handle_event({"event": "pane.agent_status_changed",
                                    "data": {"pane_id": pane, "workspace_id": pane.split(":")[0]}})

        collector.engine.set_herdr_status(True, version="0.9.1")
        collector.handle_snapshot()
        clock.advance(90 * 60)
        change("w1:p1", terminal_title_stripped="Review the checkout plan")
        clock.advance(30 * 60)
        change("w2:p1", agent_status="done")                       # finished, not reviewed
        change("w1:p2", agent_status="blocked")                    # approval
        clock.advance(5 * 60)
        change("w2:p2", agent_status="blocked")                    # question
        clock.now = now - 20 * 60
        change("w3:p1", agent_status="working")
        clock.advance(8 * 60)
        change("w3:p1", agent_status="done")                       # docs finished a turn
        change("w3:p1", agent_status="idle")                       # and it was viewed -> waiting
        if asks:   # v0.9.6 question items: three agents end a turn with a report.
            change("w3:p2", agent_status="done")                   # docs-lead: 3 questions + 1 decision
            change("w3:p3", agent_status="done")                   # release-notes: nothing to answer
            change("w3:p4", agent_status="done")                   # import-check: may want something
        # web-lead: the same screen in a check every minute for 17 minutes -> possible stall.
        clock.now = now - 18 * 60
        while clock.now < now - 60:
            collector.engine.record_output(lead, "same screen")
            clock.advance(60)
        clock.now = now - 30
        collector.engine.touch_herdr()
        collector.engine.tick()
        collector.poll_traces()
        # old-spike has no trace: its model and effort are only known from its herdr-spawn launch record.
        spike_run = str(uuid.uuid5(uuid.NAMESPACE_URL, "old-spike"))
        collector.launch_action({"run_id": spike_run, "kind": "claude", "name": "old-spike",
                                 "model": "claude-haiku-5-5", "effort": "low"})
        collector.launch_action({"run_id": spike_run, "session": f"claude:{sid('old')}"}, bind=True)
        etl = f"codex:{sid('etl')}"
        if {"w1", "w2"} <= set(watched):
            collector.relation_action({"action": "set", "subject": lead, "kind": "role", "value": "coordinator"}, local=True)
            collector.relation_action({"action": "set", "subject": lead, "kind": "team", "value": "checkout"}, local=True)
            collector.relation_action({"action": "link", "subject": ui, "kind": "reports_to", "object": lead,
                                       "registered": True}, local=True)
            collector.relation_action({"action": "link", "subject": etl, "kind": "reports_to", "object": lead}, local=True)
            collector.relation_action({"action": "link", "subject": qa, "kind": "reports_to", "object": etl}, local=True)
            collector.relation_action({"action": "link", "subject": etl, "kind": "reports_to", "object": qa}, local=True)


        # AI summaries through a stand-in CLI (no model call in tests).
        def fake_cli(command, prompt, env, timeout):
            if "Which date range" in prompt:
                raise subprocess.TimeoutExpired(command, timeout)
            summary = FAKE_SUMMARY if "Agent: ui-builder" in prompt else {
                "headline": "Reviewed the checkout plan; two open questions remain", "accomplished": ["Reviewed the plan"],
                "needs_you": {"kind": "none", "ask": "", "options": [], "why": ""}, "tests": [], "claims": [], "next_step": None}
            if any(f"Agent: {name}" in prompt for name in ("docs-lead", "release-notes", "import-check")):
                summary = dict(summary, headline="")   # no AI wording: the question items come from the rules
            return json.dumps({"type": "result", "subtype": "success", "is_error": False,
                               "result": json.dumps(summary), "usage": {"input_tokens": 900, "output_tokens": 120},
                               "modelUsage": {"claude-sonnet-test": {}}, "duration_ms": 800})
        collector.summarizer.runner = fake_cli
        collector.settings.set_override("summaries.coalesce_seconds", 0)
        collector.settings.set_override("summaries.check_git", False)
        collector.settings.set_override("summaries.auto", True)
        collector.settings.set_override("summaries.account", "claude-personal")
        collector.settings.set_override("summaries.model", "sonnet")
        collector.settings.set_override("summaries.effort", "medium")
        collector.queue_summaries()
        while (job := collector.summarizer.next_job()):
            collector.summarizer.run_job(job, collector.summary_input)

        if asks:   # Alex already answered docs-lead's first question (POST /api/asks/answer).
            collector.replies._sender = lambda pane, text: {"ok": True}   # never a real Herdr in tests
            lead_agent = next(a for a in collector.view(local=False)["agents"] if a.get("name") == "docs-lead")
            collector.answer_ask(lead_agent["asks"]["items"][0]["id"], "yes")
        view = collector.view(local=False)
        details = {a["identity"]: collector.agent_detail(a["identity"]) for a in view["agents"]}
        notifications = _notifications(view)
        view.setdefault("notifications", {"unread": sum(not n["read"] for n in notifications)})
        return {
            "notifications": notifications,
            "view": view,
            "details": details,
            "chooser": collector.chooser(),
            "output": collector.read_output(lead) if lead in details else None,
            "conversation": collector.conversation(ui, 10, None) if ui in details else None,
            "ids": {"lead": lead, "ui": ui, "qa": qa, "docs": docs, "etl": f"codex:{sid('etl')}",
                    "docs_lead": f"claude:{sid('docslead')}", "release_notes": f"claude:{sid('notes')}",
                    "import_check": f"claude:{sid('import')}"},
            "collector": collector,
        }


def _notifications(view: dict) -> list[dict]:
    """Items in the v0.8.2 /api/notifications shape, one per agent event, newest first.

    Hand-built from the view so the web tests do not depend on the API slice."""
    labels = {t["id"]: t["label"] for t in (view.get("notify_tiers") or {}).get("tiers", [])}
    items = []
    for i, a in enumerate(a for a in view["agents"] if a.get("event_id") and a.get("tier")):
        items.append({
            "id": a["event_id"], "at": a.get("event_at") or 0, "at_label": f"15 Nov 15:{40 - i:02d}",
            "tier": a["tier"], "tier_label": labels.get(a["tier"], a["tier"]),
            "what": f"{a.get('name') or a['label']}: {a.get('label') or a['kind']}",
            "why": "It cannot continue until someone answers." if a["tier"] == "waiting_on_you" else "Worth a look when you have a moment.",
            "action": "Answer the question in the terminal." if a["tier"] == "waiting_on_you" else None,
            "agent": {"identity": a["identity"], "name": a.get("name") or a["label"], "present": a.get("present", True)},
            "workspace": {"id": a["workspace_id"], "label": a["workspace_label"]},
            "coordinator": None, "evidence": a.get("snippet") or None,
            "delivery": {"state": "sent", "channels": ["phone"]},
            "gate": {"decision": "held", "reason": "Routine finish, nothing to do"} if a["tier"] == "fyi_finished" else None,
            "read": bool(a.get("acked")),
            "links": [{"kind": "pr", "label": "#12", "href": "https://example.invalid/pr/12"}] if i == 0 else [],
        })
    return sorted(items, key=lambda n: -n["at"])


def page_fixture(**kwargs) -> dict:
    data = build(**kwargs)
    data.pop("collector")
    return data
