"""Clean up sheet at 390 px: preview (v0.9.5: all unticked), tick/untick, confirm names the count and no undo, refresh-safe."""
import json
import unittest

from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run

PREVIEW = {
    "id": 7, "kind": "preview", "status": "ready", "preview_id": 7, "expires_at": 0,
    "candidates": [
        {"pane_id": "w1:p4", "workspace_id": "w1", "workspace_label": "Website", "name": "old-spike",
         "folder": "~/Projects/shop-spike", "kind": "idle",
         "reason": "Idle. Nothing unread, no open question, no background job, no uncommitted work."},
        {"pane_id": "w1:p9", "workspace_id": "w1", "workspace_label": "Website", "name": "shell",
         "folder": "~/Projects/shop", "kind": "shell", "reason": "Plain shell left from api-worker, which ended."},
    ],
    "excluded": [{"pane_id": "w1:p1", "workspace_id": "w1", "name": "web-lead", "reason": "Working."}],
    "workspaces_off": [{"workspace_id": "w4", "label": "Work", "panes": 3, "reason": "Never cleaned up."}],
}

# Answers the cleanup routes; everything else goes to the harness stub. Takes over the
# probe so it reports from inside the 390 px frame after the taps.
SCRIPT = """
window.__probed = true;
const sent = [];
const stub = window.fetch;
const preview = %s;
const offPreview = Object.assign({}, preview, {candidates: [], excluded: [],
  workspaces_off: [{workspace_id: "w2", label: "Data pipeline", panes: 2, reason: "Clean up is off for this workspace."}]});
window.fetch = async (path, init) => {
  const url = new URL(String(path), "http://x"), method = (init && init.method) || "GET";
  const reply = (body) => ({ok: true, status: 200, json: async () => body});
  if (url.pathname === UI.api.cleanup_preview) { sent.push(["preview", JSON.parse(init.body)]); return reply({id: 7, status: "running"}); }
  if (url.pathname === UI.api.cleanup && url.searchParams.get("id")) return reply(sent.at(-1)[1].workspace === "w2" ? offPreview : preview);
  if (url.pathname === UI.api.cleanup_confirm) {
    sent.push(["confirm", JSON.parse(init.body)]);
    return reply({id: 8, status: "ready", closed: [preview.candidates[1]], skipped: [], failed: []});
  }
  if (url.pathname === UI.api.cleanup) return reply({log: [{at: 1800000000, pane_id: "w1:p9", name: "shell", result: "closed"}]});
  return stub(path, init);
};
const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
const out = {innerWidth: window.innerWidth, global: !!document.querySelector('main button[data-cleanup="all"]')};
document.querySelector('main button[data-cleanup="all"]').click();
out.checking = document.querySelector("#cleanup .modal")?.textContent.includes(UI.cleanup.labels.checking);
await wait(UI.cleanup.poll_ms + 300);
const rows = [...document.querySelectorAll("#cleanup .cleanup-row")];
const confirm = () => document.querySelector("#cleanup .cleanup-confirm");
out.rows = rows.length;
out.rowHeights = rows.map((r) => Math.round(r.getBoundingClientRect().height));
out.ticked = rows.map((r) => r.querySelector("input").checked);
out.confirmText = confirm().textContent;
out.confirmDisabled = confirm().disabled;
rows[0].querySelector("input").click();
rows[1].querySelector("input").click();
out.afterTick = confirm().textContent;
rows[0].querySelector("input").click();
out.afterUntick = confirm().textContent;
// A live refresh must not rebuild the open sheet or lose the tick.
const node = document.querySelector("#cleanup .cleanup-row");
render(true); await refresh(true); render(true);
out.sameNode = node === document.querySelector("#cleanup .cleanup-row");
out.stillUnticked = !document.querySelector("#cleanup .cleanup-row input").checked;
out.stillTicked = document.querySelectorAll("#cleanup .cleanup-row input")[1].checked;
const sheet = document.querySelector("#cleanup .modal").getBoundingClientRect(), button = confirm().getBoundingClientRect();
out.sheetFits = sheet.left >= 0 && sheet.right <= window.innerWidth + 1;
out.confirmInView = button.bottom <= window.innerHeight + 1 && button.height >= 44;
out.scrollWidth = document.documentElement.scrollWidth;
out.left = document.querySelector("#cleanup .cleanup-left summary").textContent;
confirm().click();
await wait(200);
out.sent = sent;
out.result = document.querySelector("#cleanup .modal").textContent;
document.querySelector("#cleanup .actions .btn.quiet").click();
out.closedSheet = document.querySelector("#cleanup").hidden;
location.hash = hashLink("workspace", "w2");
await wait(300);
const local = document.querySelector('main button[data-cleanup="w2"]');
out.workspaceButton = local ? local.textContent : null;
local.click();
await wait(UI.cleanup.poll_ms + 300);
out.offText = document.querySelector("#cleanup .cleanup-off")?.textContent || "";
out.offButtons = [...document.querySelectorAll("#cleanup .actions button")].map((b) => b.textContent);
const target = window.parent !== window ? window.parent.document.body : document.body;
target.dataset.probe = JSON.stringify(out);
""" % json.dumps(PREVIEW)


@unittest.skipUnless(BROWSER, "no Chromium found")
class CleanupSheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = run(page_fixture(), "/list", 390, 844, script=SCRIPT)["probe"]

    def test_global_entry_opens_a_checking_sheet(self):
        self.assertEqual(self.probe.get("innerWidth"), 390, self.probe)
        self.assertTrue(self.probe["global"])
        self.assertTrue(self.probe["checking"])

    def test_candidates_start_unticked_with_tap_size(self):
        self.assertEqual(self.probe["rows"], 2)
        self.assertEqual(self.probe["ticked"], [False, False])
        self.assertEqual(self.probe["confirmText"], "Tick the panes to close")
        self.assertTrue(self.probe["confirmDisabled"])
        self.assertTrue(all(h >= 44 for h in self.probe["rowHeights"]), self.probe["rowHeights"])
        self.assertEqual(self.probe["left"], "Not included (2)")

    def test_confirm_names_the_count_and_no_undo(self):
        self.assertEqual(self.probe["afterTick"], "Close 2 pane(s). You cannot undo this.")
        self.assertEqual(self.probe["afterUntick"], "Close 1 pane(s). You cannot undo this.")
        self.assertTrue(self.probe["confirmInView"])

    def test_live_refresh_keeps_the_sheet_and_the_ticks(self):
        self.assertTrue(self.probe["sameNode"])
        self.assertTrue(self.probe["stillUnticked"])
        self.assertTrue(self.probe["stillTicked"])

    def test_fits_390_without_side_scroll(self):
        self.assertTrue(self.probe["sheetFits"])
        self.assertLessEqual(self.probe["scrollWidth"], 390)

    def test_confirm_sends_only_ticked_panes_from_the_preview(self):
        self.assertEqual(self.probe["sent"][0], ["preview", {}])
        self.assertEqual(self.probe["sent"][1], ["confirm", {"preview_id": 7, "panes": ["w1:p9"], "confirm": True}])
        self.assertIn("Closed · 1", self.probe["result"])
        self.assertIn("Recently closed · 1", self.probe["result"])
        self.assertTrue(self.probe["closedSheet"])

    def test_workspace_entry_offers_to_switch_clean_up_on(self):
        self.assertEqual(self.probe["workspaceButton"], "Clean up")
        self.assertEqual(self.probe["sent"][2], ["preview", {"workspace": "w2"}])
        self.assertIn("Clean up is off for this workspace.", self.probe["offText"])
        self.assertIn("Allow clean up here", self.probe["offText"])
        self.assertFalse(any("Close" in b and "pane" in b for b in self.probe["offButtons"]))


if __name__ == "__main__":
    unittest.main()
