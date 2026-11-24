"""v0.9.5 image preview in the question card at 390 px: thumbnail, path, viewer, choices, placeholder."""
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from herdr_attention.question_images import QuestionImages
from tests.helpers import settings
from tests.test_question_images import dialog
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER, run


def screenshot_png(width=320, height=200):
    """A small real PNG: a light page with a dark header bar, like a phone screenshot."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + (bytes((40, 44, 70)) * width if y < 36 else bytes((236, 238, 244)) * width)
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def fixture_with_image(tmp: Path) -> dict:
    """The page fixture, with the open question naming a project screenshot.

    question.image comes from the real server code; only its URLs point at the
    file, because the harness page has no server."""
    home = tmp / "home"
    shot = home / "Projects/herdr-attention-dashboard/docs/briefs/uiux2-shots/checkout-390-light.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(screenshot_png())
    data = page_fixture()
    view = data["view"]
    question = view["questions"][0]
    owner = next(a for a in view["agents"] if a["identity"] == question["identity"])
    image = QuestionImages(settings(), tmp / "cache", home=home).describe(
        question, {"prompt_text": dialog(shot), "workspace_id": owner["workspace_id"]})
    assert image and image["available"], image
    image.update(url=shot.as_uri(), thumbnail=shot.as_uri())
    question["image"] = image
    # Alex's own agent with no coordinator: a choice answers its dialog directly.
    owner["reply_target"] = {"identity": owner["identity"], "name": owner.get("name"),
                             "workspace_label": owner.get("workspace_label"), "status": owner.get("status")}
    outside = home / "Downloads/shot.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(screenshot_png())
    unavailable = QuestionImages(settings(), tmp / "cache", home=home).describe(
        question, {"prompt_text": dialog(outside), "workspace_id": owner["workspace_id"]})
    data["unavailable"] = unavailable
    data["broken"] = dict(image, id="f" * 32, thumbnail=(tmp / "gone.png").as_uri())
    return data


SCRIPT = """
window.__probed = true;
const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
const card = () => document.querySelector("main .question-card");
const rect = (el) => { const r = el.getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height), left: r.left, right: r.right}; };
const out = {innerWidth: window.innerWidth};
await wait(300);
const img = card().querySelector(".question-thumb img");
out.thumbLoaded = !!img && img.complete && img.naturalWidth > 0;
out.thumb = rect(card().querySelector(".question-thumb"));
out.shortPath = card().querySelector(".question-path .path-short").textContent;
out.fullHiddenFirst = !card().querySelector(".question-path .path-full").checkVisibility();
out.summary = rect(card().querySelector(".question-path summary"));
card().querySelector(".question-path summary").click();
const full = card().querySelector(".question-path .path-full");
out.fullPath = full.textContent;
out.full = rect(full);
out.fullShown = full.checkVisibility();
out.buttons = [...card().querySelectorAll(".quick-send")].map((b) => b.textContent);
out.hint = card().querySelector(".terminal-hint")?.textContent;
// A live refresh keeps the same image and the open path.
render(true); await refresh(true); render(true);
out.sameImage = img === card().querySelector(".question-thumb img");
out.pathOpen = card().querySelector(".question-path").open;
out.scrollWidth = document.documentElement.scrollWidth;
// Tap the thumbnail: the Latest visuals viewer opens at full size.
card().querySelector(".question-thumb").click();
const viewer = document.querySelector("#visual-viewer");
out.viewerOpen = !!viewer && viewer.open;
out.viewerSrc = viewer?.querySelector(".visual-stage img")?.getAttribute("src");
out.viewerName = viewer?.querySelector(".visual-viewer-head b")?.textContent;
viewer?.close();
await wait(50);
// A choice button sends that exact dialog option through the easy-reply route.
const sent = [], stub = window.fetch;
window.fetch = async (path, init) => {
  if (new URL(String(path), "http://x").pathname === UI.api.replies && init?.method === "POST") {
    const body = JSON.parse(init.body); sent.push(body);
    return {ok: true, status: 200, json: async () => ({status: "sent", identity: body.identity, sent_at: Date.now() / 1000, recipient: {name: "ui-builder"}})};
  }
  return stub(path, init);
};
card().querySelectorAll(".quick-send")[1].click();
await wait(200);
out.sent = sent.map((b) => ({option: b.option, text: b.text, question_id: b.question_id, identity: b.identity}));
out.questionId = FIXTURE.view.questions[0].id;
// Outside the allow-list: the path and the buttons stay, the preview is a placeholder.
FIXTURE.view.questions[0].image = FIXTURE.unavailable;
await refresh(true); render(true);
out.placeholder = card().querySelector(".question-thumb.unavailable")?.textContent || "";
out.placeholderPath = card().querySelector(".question-path .path-short")?.textContent;
out.placeholderButtons = card().querySelectorAll(".quick-send").length;
out.noImg = !card().querySelector(".question-thumb img");
// An image that fails to load shows the placeholder too.
FIXTURE.view.questions[0].image = FIXTURE.broken;
await refresh(true); render(true);
await wait(300);
out.broken = card().querySelector(".question-thumb.unavailable")?.textContent || "";
const target = window.parent !== window ? window.parent.document.body : document.body;
target.dataset.probe = JSON.stringify(out);
"""


@unittest.skipUnless(BROWSER, "no Chromium found")
class QuestionPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = fixture_with_image(Path(cls.tmp.name))
        cls.probe = run(cls.data, "/list", 390, 844, script=SCRIPT)["probe"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_thumbnail_sits_in_the_card_and_fits_390(self):
        self.assertEqual(self.probe.get("innerWidth"), 390, self.probe)
        self.assertTrue(self.probe["thumbLoaded"])
        thumb = self.probe["thumb"]
        self.assertGreaterEqual(thumb["left"], 0)
        self.assertLessEqual(thumb["right"], 390)
        self.assertGreaterEqual(thumb["h"], 44)
        self.assertLessEqual(self.probe["scrollWidth"], 390)

    def test_path_is_short_and_shows_in_full_on_tap(self):
        self.assertEqual(self.probe["shortPath"], "~/Projects/…/uiux2-shots/checkout-390-light.png")
        self.assertTrue(self.probe["fullHiddenFirst"])
        self.assertGreaterEqual(self.probe["summary"]["h"], 44)
        self.assertEqual(self.probe["fullPath"],
                         "~/Projects/herdr-attention-dashboard/docs/briefs/uiux2-shots/checkout-390-light.png")
        self.assertTrue(self.probe["fullShown"])
        self.assertGreater(self.probe["full"]["h"], 0)
        self.assertLessEqual(self.probe["full"]["right"], 391)

    def test_choices_are_the_dialog_options_only(self):
        self.assertEqual(self.probe["buttons"], self.data["view"]["questions"][0]["options"])
        self.assertIn("Choose an option", self.probe["hint"])
        option = self.data["view"]["questions"][0]["options"][1]
        self.assertEqual(self.probe["sent"], [{"option": option, "text": option, "question_id": self.probe["questionId"],
                                               "identity": self.data["view"]["questions"][0]["identity"]}])

    def test_live_refresh_keeps_the_image_and_the_open_path(self):
        self.assertTrue(self.probe["sameImage"])
        self.assertTrue(self.probe["pathOpen"])

    def test_tap_opens_the_latest_visuals_viewer(self):
        self.assertTrue(self.probe["viewerOpen"])
        self.assertEqual(self.probe["viewerSrc"], self.data["view"]["questions"][0]["image"]["url"])
        self.assertEqual(self.probe["viewerName"], "checkout-390-light.png")

    def test_outside_or_broken_image_shows_the_placeholder_path_and_buttons(self):
        self.assertIn("Preview not available", self.probe["placeholder"])
        self.assertIn("The file is outside your project folders.", self.probe["placeholder"])
        self.assertEqual(self.probe["placeholderPath"], "~/Downloads/shot.png")
        self.assertEqual(self.probe["placeholderButtons"], 3)
        self.assertTrue(self.probe["noImg"])
        self.assertIn("The image did not load.", self.probe["broken"])


if __name__ == "__main__":
    unittest.main()
