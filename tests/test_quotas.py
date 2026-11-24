"""Read-only mirror of Omarchy agent-usage quota records."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from herdr_attention.quotas import has_subscription, read_quotas, window_title
from tests.helpers import FIXTURES, settings
from tests.test_api import make_collector

USAGE = FIXTURES / "quotas" / "usage"
ORDER = FIXTURES / "quotas" / "order.json"
NOW = datetime(2026, 11, 15, 12, 21, tzinfo=ZoneInfo("Europe/Amsterdam"))
TZ = "Europe/Amsterdam"


def snapshot(**kwargs):
    return read_quotas(usage_dir=USAGE, order_path=ORDER, tz_name=TZ, now=NOW, **kwargs)


class QuotaReader(unittest.TestCase):
    def setUp(self):
        self.data = snapshot()
        self.by_id = {card["id"]: card for card in self.data["accounts"]}

    def test_source_and_timezone(self):
        self.assertEqual(self.data["source"], "omarchy-agent-usage")
        self.assertEqual(self.data["timezone"], "Europe/Amsterdam")
        self.assertIsNone(self.data["error"])
        self.assertTrue(self.data["accounts"])

    def test_mirrors_panel_subscriptions_in_saved_order(self):
        ids = [card["id"] for card in self.data["accounts"]]
        # Claude's two accounts first, then Codex, Grok Bot, Cursor, Agy.
        # Copilot has a plan so it is a subscription; leftover claude-personal
        # and history-only DeepSeek are not.
        self.assertEqual(ids[:7], [
            "claude:main", "claude:personal", "codex", "codex-personal:personal", "grokbot", "cursor", "agy",
        ])
        self.assertIn("copilot", ids)
        self.assertNotIn("claude-personal", ids)
        self.assertNotIn("deepseek", ids)
        self.assertNotIn("muse", ids)
        self.assertFalse(has_subscription(json.loads((USAGE / "claude-personal.json").read_text())))
        self.assertFalse(has_subscription(json.loads((USAGE / "deepseek.json").read_text())))

    def test_claude_accounts_and_windows(self):
        main = self.by_id["claude:main"]
        self.assertEqual(main["provider_name"], "Claude Code")
        self.assertEqual(main["label"], "Main")
        self.assertEqual(main["plan"], "Team 5x")
        self.assertFalse(main["active"])
        self.assertTrue(main["primary"])
        self.assertTrue(main["stale"])
        self.assertEqual(main["status"], "Limits paused")
        weekly = next(w for w in main["windows"] if w["title"] == "Weekly")
        self.assertEqual(weekly["used_pct"], 94)
        self.assertEqual(weekly["resets_label"], "Wed 05:59")
        session = next(w for w in main["windows"] if w["title"] == "Session")
        self.assertTrue(session["expired"] or session["used_pct"] == 0)

        personal = self.by_id["claude:personal"]
        self.assertEqual(personal["plan"], "Pro")
        self.assertTrue(personal["active"])
        self.assertFalse(personal["stale"])
        session = next(w for w in personal["windows"] if w["title"] == "Session")
        self.assertEqual(session["used_pct"], 16)
        self.assertEqual(session["resets_label"], "16:50")
        weekly = next(w for w in personal["windows"] if w["title"] == "Weekly")
        self.assertEqual(weekly["used_pct"], 29)
        self.assertEqual(weekly["resets_label"], "Mon 20:00")
        fable = next(w for w in personal["windows"] if w["title"] == "Fable Weekly")
        self.assertEqual(fable["used_pct"], 58)

    def test_codex_agy_cursor_and_expired_window(self):
        codex = self.by_id["codex"]
        self.assertEqual(codex["plan"], "Business Premium")
        self.assertEqual(codex["label"], "Work")
        self.assertEqual(codex["windows"][0]["used_pct"], 18)
        self.assertEqual(codex["windows"][0]["resets_label"], "Fri 22:28")

        personal = self.by_id["codex-personal:personal"]
        self.assertEqual(personal["provider_name"], "Codex")
        self.assertEqual(personal["label"], "Personal")
        self.assertEqual(personal["plan"], "Plus")
        session = next(w for w in personal["windows"] if w["title"] == "Session")
        self.assertEqual(session["used_pct"], 0)
        self.assertEqual(session["resets_label"], "17:42")
        weekly = next(w for w in personal["windows"] if w["title"] == "Weekly")
        self.assertEqual(weekly["used_pct"], 49)
        self.assertEqual(weekly["resets_label"], "15:34")

        agy = self.by_id["agy"]
        self.assertEqual(agy["plan"], "Gemini Enterprise Plus")
        self.assertEqual(agy["windows"], [])
        self.assertIn("no quota meters", agy["note"].lower())

        cursor = self.by_id["cursor"]
        self.assertEqual(cursor["plan"], "Team")
        named = next(w for w in cursor["windows"] if "Named" in w["title"])
        self.assertEqual(named["used_pct"], 100)

        copilot = self.by_id["copilot"]
        self.assertTrue(copilot["stale"])
        self.assertEqual(copilot["windows"][0]["used_pct"], 0)
        self.assertTrue(copilot["windows"][0]["expired"])
        self.assertIsNone(copilot["windows"][0]["resets_at"])

    def test_never_leaks_emails_or_tokens(self):
        blob = json.dumps(self.data)
        for needle in (
            "work-account@example.com",
            "personal-account@example.com",
            "copilot-secret@example.com",
            "tok_should_never_leak",
            "access_token",
            "example.com",
        ):
            self.assertNotIn(needle, blob)
        self.assertNotIn("email", blob)

    def test_missing_directory_is_empty_not_an_exception(self):
        missing = Path("/tmp/herdr-attention-quotas-absent")
        data = read_quotas(usage_dir=missing, now=NOW, tz_name=TZ)
        self.assertEqual(data["accounts"], [])
        self.assertEqual(data["error"], "usage records not found")

    def test_corrupt_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "claude.json").write_text("{not-json")
            (path / "codex.json").write_text(json.dumps({
                "id": "codex", "name": "Codex", "tierLabel": "Pro",
                "limits": [{"label": "Weekly", "percent": 0.2, "resetsAt": "2026-11-20T21:00:00+00:00"}],
            }))
            data = read_quotas(usage_dir=path, now=NOW, tz_name=TZ)
            self.assertEqual([c["id"] for c in data["accounts"]], ["codex"])

    def test_window_titles(self):
        self.assertEqual(window_title("Session (5-hour)"), "Session")
        self.assertEqual(window_title("5h window"), "Session")
        self.assertEqual(window_title("Weekly (7-day)"), "Weekly")
        self.assertEqual(window_title("Included total", "Monthly"), "Monthly")
        self.assertEqual(window_title("Fable Weekly", "Fable Weekly"), "Fable Weekly")


class QuotaInView(unittest.TestCase):
    def test_view_and_api_include_mirrored_quotas(self):
        collector = make_collector(quotas={
            "usage_dir": str(USAGE),
            "order_path": str(ORDER),
            "timezone": TZ,
        })
        view = collector.view(local=True)
        self.assertEqual(view["quotas"]["source"], "omarchy-agent-usage")
        ids = [card["id"] for card in view["quotas"]["accounts"]]
        self.assertIn("claude:personal", ids)
        self.assertIn("codex", ids)
        self.assertIn("agy", ids)
        self.assertNotIn("email", json.dumps(view["quotas"]))

    def test_default_test_settings_do_not_read_live_records(self):
        collector = make_collector()
        view = collector.view(local=True)
        self.assertEqual(view["quotas"]["accounts"], [])
        self.assertEqual(view["quotas"]["error"], "usage records not found")


if __name__ == "__main__":
    unittest.main()


class QuotaSummaryWording(unittest.TestCase):
    def test_one_summary_and_plain_wording(self):
        from herdr_attention.quotas import enrich, summary
        q = enrich(snapshot(), [])
        self.assertEqual(q['header']['label'], q['summary']['label'])
        for a in q['accounts']:
            self.assertNotIn('ago ago', a['freshness'])
        cursor = next(a for a in q['accounts'] if a['id'] == 'cursor')
        self.assertEqual(q['summary']['account'], 'cursor')
        # Inactive accounts never win the summary, even when full.
        for a in q['accounts']:
            a['active'] = a['id'] != 'cursor'
        self.assertNotEqual(summary(q['accounts'])['account'], 'cursor')
        self.assertEqual(summary([])['label'], 'Quotas')


class GateLogWording(unittest.TestCase):
    def test_gate_log_leads_with_the_alert_text_not_a_shouted_decision(self):
        source = (Path(__file__).resolve().parent.parent / 'herdr_attention/web/app.js').read_text()
        body = source[source.index('function notifyGateCard'):source.index('async function sendNtfyTest')]
        self.assertNotIn('toUpperCase', body)
        self.assertIn('decision_labels', body)
        self.assertIn('r.title || r.reason', body)


class VisualsWait(unittest.TestCase):
    def test_latest_visuals_stops_waiting_and_says_what_to_do(self):
        source = (Path(__file__).resolve().parent.parent / 'herdr_attention/web/app.js').read_text()
        body = source[source.index('const VISUALS_WAIT_MS'):source.index('function openVisual')]
        self.assertIn('AbortSignal.timeout(VISUALS_WAIT_MS)', body)
        self.assertIn('VISUALS_SLOW', body)


class VisualsPartialAndFocus(unittest.TestCase):
    source = (Path(__file__).resolve().parent.parent / 'herdr_attention/web/app.js').read_text()

    def test_partial_scan_gets_one_muted_line(self):
        body = self.source[self.source.index('function visualsStrip'):self.source.index('function openVisual')]
        self.assertIn('data.partial ?', body)
        self.assertIn('Not all folders were scanned', body)
        self.assertIn('data.partial_reason', body)

    def test_open_in_herdr_needs_a_pane(self):
        body = self.source[self.source.index('function openInHerdrButton'):self.source.index('async function desktopNavigation')]
        self.assertIn('!agent.pane_id) return null', body)


class GaugeData(unittest.TestCase):
    """v0.9.4 radial gauges: stable order, rings, flags, plain age, history."""

    def test_order_is_fixed_and_urgency_is_only_a_flag(self):
        from herdr_attention.quotas import enrich
        calm = enrich(snapshot(), [])
        hot = snapshot()
        for card in hot['accounts']:
            if card['id'] == 'grokbot':
                for w in card['windows']:
                    w['used_pct'] = 100
        hot = enrich(hot, [])
        self.assertEqual(calm['account_ids'], hot['account_ids'])
        self.assertEqual([a['id'] for a in hot['accounts']], hot['account_ids'])
        grok = next(a for a in hot['accounts'] if a['id'] == 'grokbot')
        self.assertEqual(grok['flag']['id'], 'full')
        self.assertFalse(grok['healthy'])

    def test_rings_weekly_outside_session_inside(self):
        from herdr_attention.quotas import gauge_rings
        rings = gauge_rings([{'title': 'Session', 'used_pct': 10}, {'title': 'Weekly', 'used_pct': 40},
                             {'title': 'Fable Weekly', 'used_pct': 70}])
        # 0.9.8.7: a model-only window (Fable Weekly) never takes a ring; it stops only that model's agents.
        self.assertEqual([(r['title'], r['pct'], r['level'], r['unit']) for r in rings],
                         [('Weekly', 40, 'low', 'wk'), ('Session', 10, 'low', '5h')])
        own = gauge_rings([{'title': 'Monthly', 'used_pct': 93}, {'title': 'Named models Monthly', 'used_pct': 100}])
        self.assertEqual([(r['pct'], r['unit']) for r in own], [(93, 'mo')])
        only = gauge_rings([{'title': 'Fable Weekly', 'used_pct': 70}])
        self.assertEqual([r['pct'] for r in only], [70], "with no own window the model window still shows")
        self.assertEqual(gauge_rings([]), [])

    def test_stopped_agent_flags_the_account_and_counts_agents(self):
        from herdr_attention.quotas import enrich
        agents = [{'identity': 'a1', 'name': 'w1', 'present': True, 'quota_wall': {'provider': 'codex'},
                   'launch': {'account': 'codex-work'}},
                  {'identity': 'a2', 'name': 'w2', 'present': True, 'launch': {'account': 'codex-work'}}]
        q = enrich(snapshot(), agents)
        work = next(a for a in q['accounts'] if a['id'] == 'codex')
        self.assertEqual(work['flag'], {'id': 'stopped', 'label': '1 agent stopped'})   # 0.9.8.7: says how many
        self.assertEqual((work['agent_count'], work['agents_label']), (2, '2 agents'))

    def test_checked_age_is_plain(self):
        from herdr_attention.quotas import checked_label
        self.assertEqual(checked_label(2400), 'checked 40 min ago')
        self.assertEqual(checked_label(3600 * 4 + 660), 'checked 4 h 11 min ago')
        self.assertEqual(checked_label(20), 'checked just now')
        self.assertEqual(checked_label(None), 'check time unknown')

    def test_history_keeps_daily_message_counts_only(self):
        from herdr_attention.quotas import _history
        days = [{'date': f'2026-11-{d:02d}', 'messageCount': d, 'totalTokens': 9} for d in range(1, 10)]
        out = _history({'recentDays': days})
        self.assertEqual(len(out), 7)
        self.assertEqual(out[-1], {'date': '2026-11-09', 'messages': 9})
        self.assertEqual(_history({}), [])

    def test_labels_come_from_the_contract(self):
        from herdr_attention.quotas import LABELS
        from herdr_attention.ui_contract import CONTRACT
        self.assertIs(LABELS, CONTRACT['quotas']['labels'])


class SummaryFollowsArrange(unittest.TestCase):
    def test_hidden_accounts_never_head_the_summary_and_arrange_pins_win(self):
        from herdr_attention.quotas import enrich, summary
        q = enrich(snapshot(), [], [], {'account_hidden': ['cursor'], 'account_pinned': ['grokbot']})
        self.assertNotEqual(q['summary']['account'], 'cursor')
        # Main (94 %, active in the fixture) is the only shown hot account left.
        hot = [a['id'] for a in q['accounts'] if a['id'] != 'cursor' and a.get('active') is not False and a['worst_pct'] >= 90]
        self.assertEqual(q['summary']['account'], hot[0] if hot else 'grokbot')
        calm = [dict(a, worst_pct=min(a['worst_pct'], 50)) for a in q['accounts']]
        self.assertEqual(summary(calm, ['codex'], {'account_pinned': ['grokbot']})['account'], 'grokbot')
        self.assertEqual(summary(calm, ['codex'], {})['account'], 'codex')
        self.assertNotEqual(summary(calm, ['codex'], {'account_hidden': ['codex']})['account'], 'codex')
