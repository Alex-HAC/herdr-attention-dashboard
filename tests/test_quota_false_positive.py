"""0.9.6.2: "Out of quota" only from the provider's own limit banner, never from an agent's own text."""
import unittest

from herdr_attention.linking import quota_banner, quota_signal
from herdr_attention.quota_stops import QuotaStops
from herdr_attention.store import Store
from herdr_attention import team_reports

# What agents that BUILD quota features have on their screens (from the wC panes, Nov 17).
PROSE = """● The quota worker is done: own warning levels per account, usage limit and token limit
  checks, "quota exceeded" in tests, and the 5-hour limit ring.

● Bash(pytest tests/test_quotas.py -k "usage limit reached")
  ⎿  assert quota_signal("You've hit your usage limit")
     + r'usage limit reached|rate_limit_exceeded'
     test_out_of_quota_banner PASSED
     12 passed in 0.4s

● I told the worker that "Claude usage limit reached" must not match inside a sentence, and that
  the account is out of quota only when the banner says so.
  - nearly full accounts are flagged; a full account (weekly limit reached earlier) never moves
  1. Should the alert say you've hit your usage limit? (quoted text, not a banner)

✻ Churned for 3m 12s
───────────────────────────────────────
❯
───────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)
"""
CLAUDE_BANNER = """● Running the full suite now.

  ⎿  Claude usage limit reached. Your limit will reset at 8pm (Europe/Amsterdam).

───────────────────────────────────────
❯
───────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)
"""
CLAUDE_BANNER_2 = "● Working on it\n\n 5-hour limit reached ∙ resets 3pm\n /upgrade to increase your usage limit.\n❯ \n"
CODEX_BANNER = """• Ran pytest -q

■ You've hit your usage limit. Upgrade to Pro (https://openai.com/chatgpt/pricing) or try again at 4:12 PM.

› Ask Codex to do anything
"""
CURSOR_BANNER = "  You've reached your premium request limit for this month.\n  > \n"
API_BANNER = "API Error: 429 {\"type\":\"error\",\"error\":{\"type\":\"rate_limit_error\",\"message\":\"usage_limit_reached\"}}\n❯ \n"


def agent(identity="claude:a", status="idle", **extra):
    return {"identity": identity, "status": status, "workspace_id": "w1", "pane_id": "w1:p1", "last_activity_at": 5, **extra}


class Banner(unittest.TestCase):
    def test_an_agent_writing_about_quotas_is_not_out_of_quota(self):
        self.assertFalse(quota_banner(PROSE))

    def test_real_banners_are_found(self):
        for text in (CLAUDE_BANNER, CLAUDE_BANNER_2, CODEX_BANNER, CURSOR_BANNER, API_BANNER):
            self.assertTrue(quota_banner(text), text)

    def test_lines_an_agent_writes_about_the_banner_do_not_count(self):
        for line in ("- Usage limit reached is now read only from the banner",
                     "⏺ Usage limit reached banners are matched at the line start",
                     "● Usage limit reached. That is the banner text we look for",
                     "+    usage_limit_reached = True", "usage_limit_reached = True",
                     "| usage limit reached | yes |", "# Out of credits handling",
                     "> You've hit your usage limit", "Errors: fixed the 'usage limit reached' test",
                     "Usage limit reached is now read only from the banner",
                     "1. You've hit your usage limit", "Too many requests were sent by the test",
                     "error: test_usage_limit_reached failed"):
            self.assertFalse(quota_banner("● Done.\n" + line + "\n❯ \n"), line)

    def test_more_real_banner_wordings(self):
        for text in ("Opus weekly limit reached ∙ resets Mon 9am", "Limit reached · resets 7pm",
                     "You're out of extra usage · resets 3pm", "Credit balance is too low",
                     "Too many requests", "Weekly limit reached",
                     "You've reached your usage limit for GPT-5.", "API Error: Rate limit reached",
                     "  ⎿  API Error: 429 Too many requests",
                     '{"type":"error","error":{"type":"rate_limit_error","message":"This request would exceed your rate limit"}}',
                     "■ You've hit your usage\nlimit. Upgrade to Pro or try again at 4:12 PM.",     # wrapped by a narrow pane
                     "Claude usage limit reached. Your limit will reset at 8pm.\n" + "\n".join(["  hint"] * 12)):
            self.assertTrue(quota_banner("● Working\n\n" + text + "\n───────\n❯ \n───────\n"), text)

    def test_a_banner_that_scrolled_far_up_no_longer_counts(self):
        self.assertFalse(quota_banner(CODEX_BANNER + "\n".join(f"line {i}" for i in range(30))))

    def test_alert_titles_need_the_provider_words_too(self):
        for title in ("Quota settings done: usage limit levels per account", "Token limit test added",
                      "Rate limit handling reviewed", "Quota exceeded case covered by tests"):
            self.assertFalse(quota_signal(title), title)
        for title in ("Stopped: you've hit your usage limit", "Out of quota", "Codex is out of quota",
                      "Stack quickstart guide is blocked by usage limit"):
            self.assertTrue(quota_signal(title), title)


class Stops(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.store = Store(":memory:")
        self.stops = QuotaStops(self.store, lambda: self.now[0])

    def active(self, identity="claude:a"):
        row = self.stops.get(identity)
        return bool(row and row["active"])

    def test_prose_never_stops_an_agent(self):
        self.stops.observe(agent(), PROSE, [])
        self.assertFalse(self.active())

    def test_banner_stops_an_idle_agent_and_work_clears_it(self):
        self.stops.observe(agent(), CLAUDE_BANNER, [])
        self.assertTrue(self.active())
        self.stops.observe(agent(status="working"), CLAUDE_BANNER, [])   # same screen, but it works
        self.assertFalse(self.active())

    def test_a_working_agent_is_never_stopped(self):
        self.stops.observe(agent(status="working"), CODEX_BANNER, [])
        self.assertFalse(self.active())

    def test_a_real_stop_comes_back_after_a_cleanup_or_a_short_working_spell(self):
        self.stops.observe(agent(), CLAUDE_BANNER, [])
        self.stops.clear("claude:a")                                   # the one-time cleanup, or prune
        self.stops.observe(agent(), CLAUDE_BANNER, [])                 # same screen, still idle
        self.assertTrue(self.active())
        self.stops.observe(agent(status="working"), CLAUDE_BANNER, [])
        self.stops.observe(agent(), CLAUDE_BANNER, [])
        self.assertTrue(self.active())

    def test_stops_of_agents_that_are_gone_end(self):
        self.stops.observe(agent("claude:gone"), CLAUDE_BANNER, [])
        self.stops.observe(agent("claude:here"), CLAUDE_BANNER, [])
        self.assertEqual(self.stops.prune({"claude:here"}), ["claude:gone"])
        self.assertFalse(self.active("claude:gone"))
        self.assertTrue(self.active("claude:here"))


class Team(unittest.TestCase):
    def view(self, stopped, statuses=None):
        statuses = statuses or {}
        ids = ["claude:lead", "claude:w1", "claude:w2"]
        agents = [{"identity": i, "name": i, "present": True, "status": statuses.get(i, "working"),
                   "state": "working", "quota_stop": {"x": 1} if i in stopped else None} for i in ids]
        links = [{"kind": "reports_to", "from": i, "to": "claude:lead"} for i in ids[1:]]
        return {"agents": agents, "links": links, "questions": []}

    def status(self, stopped, statuses=None):
        return team_reports.facts(self.view(stopped, statuses), "claude:lead")["status"]

    def test_one_stopped_member_does_not_block_the_team(self):
        view = self.view({"claude:w1"})
        facts = team_reports.facts(view, "claude:lead")
        self.assertEqual(facts["status"], "stuck")
        self.assertIn("claude:w1", [s["identity"] for s in facts["stuck"]])

    def test_blocked_when_the_lead_or_everyone_at_work_is_stopped(self):
        self.assertEqual(self.status({"claude:lead"}), "blocked on quota")
        self.assertEqual(self.status({"claude:lead", "claude:w1", "claude:w2"}), "blocked on quota")
        self.assertEqual(self.status({"claude:w1", "claude:w2"}, {"claude:lead": "done"}), "blocked on quota")
        self.assertEqual(self.status(set()), "on track")


if __name__ == "__main__":
    unittest.main()
