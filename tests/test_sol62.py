import unittest

from herdr_attention import llm
from herdr_attention.config import DEFAULTS


class Sol62Defaults(unittest.TestCase):
    def test_codex_accounts_default_to_sol_62(self):
        for account in llm.account_catalog():
            if account.provider == "codex":
                self.assertEqual(account.default_model, "gpt-6.2-sol")
                self.assertEqual(account.models[0][0], "gpt-6.2-sol")

    def test_summary_and_gate_defaults_are_sol_62_low(self):
        self.assertEqual((DEFAULTS["summaries"]["model"], DEFAULTS["summaries"]["effort"]), ("gpt-6.2-sol", "low"))
        self.assertEqual((DEFAULTS["notify_gate"]["model"], DEFAULTS["notify_gate"]["effort"]), ("gpt-6.2-sol", "low"))

    def test_a_saved_sol_61_choice_still_validates(self):
        account = llm.resolve_account({"summaries.account": "codex-personal"})
        self.assertEqual(llm.default_model_for(account, {"summaries.model": "gpt-6.1-sol"}), ("gpt-6.1-sol", "low"))
        self.assertEqual(llm.default_model_for(account, {}), ("gpt-6.2-sol", "low"))


if __name__ == "__main__":
    unittest.main()
