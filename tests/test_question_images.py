"""v0.9.5 image preview inside questions: what the server shows, refuses and serves."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from herdr_attention.collector import Collector
from herdr_attention.question_images import (MAX_BYTES, REASONS, QuestionImages, image_paths, sniff,
                                             validate_folders)
from tests.helpers import FakeHerdr, agent, settings, workspaces

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89" \
      b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"


def dialog(path):
    """Claude Code's permission dialog for reading a file outside the working folder."""
    return (" Read file\n\n"
            f"  Read({path})\n\n"
            " Do you want to proceed?\n"
            " ❯ 1. Yes\n"
            "   2. Yes, allow reading from shots/ during this session\n"
            "   3. No, and tell Claude what to do differently (esc)")


class Home:
    """A throwaway home folder with Projects, Downloads and a private Work folder."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        for name in ("Projects/site/shots", "Pictures", "Downloads", "Work/private"):
            (self.home / name).mkdir(parents=True)

    def file(self, relative, data=PNG):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class Paths(unittest.TestCase):
    def test_reads_the_path_of_the_read_call_also_when_it_wraps(self):
        self.assertEqual(image_paths(dialog("/home/a/Projects/x/shot.png"))[0], "/home/a/Projects/x/shot.png")
        wrapped = "  Read(/home/a/Projects/site/docs/cleanup-\n  390-light.png)\n Do you want to proceed?"
        self.assertEqual(image_paths(wrapped)[0], "/home/a/Projects/site/docs/cleanup-390-light.png")
        spaced = "  Read(/home/a/Projects/x/Screenshot from 2026 (1).png)\n Do you want to proceed?"
        self.assertEqual(image_paths(spaced)[0], "/home/a/Projects/x/Screenshot from 2026 (1).png")

    def test_the_dialog_path_wins_over_older_output_above_it(self):
        screen = "Saved /home/a/Projects/old.png earlier.\n" + dialog("~/Projects/new.jpg")
        self.assertEqual(image_paths(screen)[:2], ["~/Projects/new.jpg", "/home/a/Projects/old.png"])

    def test_plain_question_text_and_nothing_found(self):
        self.assertEqual(image_paths("May I open ~/Projects/site/home.webp, please?"), ["~/Projects/site/home.webp"])
        self.assertEqual(image_paths("Run npm test in ./cart?"), [])
        self.assertEqual(image_paths("x" * 20000 + " /a/b.png"), [])
        self.assertEqual(image_paths(None), [])

    def test_any_screen_is_scanned_in_bounded_time(self):
        for text in ("a=/b" * 2000, "Read(" * 1600, ("Read(/" + "a" * 4000 + "\n") * 2, "/" + "a/" * 4000 + ".png"):
            started = time.perf_counter()
            image_paths(text)
            self.assertLess(time.perf_counter() - started, 0.5, text[:20])

    def test_type_comes_from_the_content(self):
        self.assertEqual(sniff(PNG), "image/png")
        self.assertEqual(sniff(b"\xff\xd8\xff\xe0rest"), "image/jpeg")
        self.assertEqual(sniff(b"GIF89a..."), "image/gif")
        self.assertEqual(sniff(b"RIFF\x10\x00\x00\x00WEBPVP8 "), "image/webp")
        self.assertIsNone(sniff(b"<svg xmlns="))
        self.assertIsNone(sniff(b"#!/bin/sh\n"))


class Rules(unittest.TestCase):
    def setUp(self):
        self.h = Home()
        self.addCleanup(self.h.tmp.cleanup)
        self.images = QuestionImages(settings(), self.h.root / "cache", home=self.h.home)

    def check(self, raw, workspace="w1", cwd=None):
        return self.images.inspect(raw, workspace=workspace, cwd=cwd).reason

    def test_an_image_in_a_project_folder_is_shown(self):
        self.h.file("Projects/site/shots/home.png")
        found = self.images.inspect("~/Projects/site/shots/home.png", workspace="w1", cwd=None)
        self.assertIsNone(found.reason)
        self.assertEqual((found.mime, found.size), ("image/png", len(PNG)))

    def test_relative_paths_use_the_agent_folder(self):
        self.h.file("Projects/site/shots/home.png")
        self.assertIsNone(self.check("shots/home.png", cwd=str(self.h.home / "Projects/site")))
        self.assertEqual(self.check("shots/home.png"), "outside")

    def test_outside_the_allow_list_and_symlink_escapes_are_refused(self):
        outside = self.h.file("Downloads/shot.png")
        self.assertEqual(self.check(str(outside)), "outside")
        (self.h.home / "Projects/site/shots/link.png").symlink_to(outside)
        self.assertEqual(self.check("~/Projects/site/shots/link.png"), "outside")
        (self.h.home / "Projects/site/downloads").symlink_to(self.h.home / "Downloads")
        self.assertEqual(self.check("~/Projects/site/downloads/shot.png"), "outside")
        self.assertEqual(self.check("~/Projects/site/../../Downloads/shot.png"), "outside")

    def test_private_files_and_private_workspace_are_never_shown(self):
        secret = self.h.file("Work/private/board.png")
        self.assertEqual(self.check(str(secret)), "private")
        (self.h.home / "Projects/site/private.png").symlink_to(secret)
        self.assertEqual(self.check("~/Projects/site/private.png"), "private")
        # Even when someone puts the private work folder in the allow-list by hand.
        self.images.settings.set_override("questions.image_folders", ["~/Work", "~/Projects"])
        self.assertEqual(self.check(str(secret)), "private")
        self.h.file("Projects/site/shots/home.png")
        self.assertEqual(self.check("~/Projects/site/shots/home.png", workspace="w4"), "workspace")

    def test_type_size_and_missing(self):
        self.h.file("Projects/site/fake.png", b"#!/bin/sh\necho not an image\n")
        self.assertEqual(self.check("~/Projects/site/fake.png"), "type")
        (self.h.home / "Projects/site/folder.png").mkdir()
        self.assertEqual(self.check("~/Projects/site/folder.png"), "type")
        big = self.h.file("Projects/site/big.png")
        with big.open("r+b") as fh:
            fh.truncate(MAX_BYTES + 1)
        self.assertEqual(self.check("~/Projects/site/big.png"), "size")
        self.assertEqual(self.check("~/Projects/site/gone.png"), "missing")

    def test_pictures_is_allowed_by_default_with_the_same_checks(self):
        self.h.file("Pictures/Screenshots/shot.png")
        self.assertIsNone(self.check("~/Pictures/Screenshots/shot.png"))
        self.h.file("Pictures/note.png", b"<html>not an image</html>")
        self.assertIsNotNone(self.check("~/Pictures/note.png"))
        (self.h.home / "Pictures/out.png").symlink_to(self.h.file("Downloads/secret.png"))
        self.assertEqual(self.check("~/Pictures/out.png"), "outside")
        (self.h.home / "Pictures/work.png").symlink_to(self.h.file("Work/private/w.png"))
        self.assertEqual(self.check("~/Pictures/work.png"), "private")
        self.h.file("Pictures/private-demo/a.png")
        self.assertEqual(self.check("~/Pictures/private-demo/a.png"), "private")
        self.assertEqual(self.check("~/Pictures/Screenshots/shot.png", workspace="w4"), "workspace")

    def test_private_named_folders_inside_an_allowed_folder_are_never_shown(self):
        for name in ("private-registry", "private_system", "ctrl-private144", "PRIVATE-tools"):
            self.h.file(f"Projects/{name}/shots/a.png")
            self.assertEqual(self.check(f"~/Projects/{name}/shots/a.png"), "private", name)
        self.h.file("Projects/site/private/a.png")
        self.assertEqual(self.check("~/Projects/site/private/a.png"), "private")
        for name in ("privateer", "unprivate", "myprivate"):   # not private: no separator around "private"
            self.h.file(f"Projects/{name}/a.png")
            self.assertIsNone(self.check(f"~/Projects/{name}/a.png"), name)

    def test_folders_setting_refuses_private_and_too_broad_folders(self):
        self.assertEqual(validate_folders(["~/Projects", "~/Projects/", "/srv/projects"]), ["~/Projects", "/srv/projects"])
        self.assertEqual(validate_folders([]), [])
        for bad in (["/"], ["~"], ["~/"], ["/home"], ["~/Work"], ["~/Work/private"], [str(Path.home())],
                    ["Projects"], ["~/Projects/../Work"], "~/Projects", [3]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_folders(bad)

    def test_folders_setting_is_not_editable_from_the_phone(self):
        s = settings()
        with self.assertRaises(PermissionError):
            s.validate("questions.image_folders", ["~/Projects"], from_phone=True)
        self.assertEqual(s.validate("questions.image_folders", ["~/Projects"]), ["~/Projects"])
        self.assertEqual(s.get("questions.image_folders"), ["~/Projects", "~/Pictures"])


def blocked_rig(test):
    """A collector with one Claude agent stopped at the Read dialog for a project screenshot."""
    test.h = Home()
    test.addCleanup(test.h.tmp.cleanup)
    test.shot = test.h.file("Projects/site/shots/home.png")
    test.herdr = FakeHerdr([agent("w1:p1", "blocked", session="ask", name="site")], workspaces("w1", "w4"))
    test.herdr.screens["w1:p1"] = dialog(test.shot)
    test.herdr.explain_rules["w1:p1"] = "permission_prompt"
    test.c = Collector(db_path=":memory:", client=test.herdr, settings=settings(), clock=time.time)
    test.addCleanup(test.c.store.close)
    test.c.question_images.home = test.h.home
    test.c.question_images.cache = test.h.root / "cache"
    test.c.handle_snapshot()


def open_question(c):
    return next(q for q in c.view(local=False)["questions"] if q["identity"] == "claude:ask")


class Served(unittest.TestCase):
    """Through the real collector: the question gets an image, and only that image is served."""

    def setUp(self):
        blocked_rig(self)

    def question(self):
        return open_question(self.c)

    def status(self, value):
        self.herdr.set(agent("w1:p1", value, session="ask", name="site"))
        self.c.handle_event({"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "workspace_id": "w1"}})

    def test_question_carries_the_image_and_the_dialog_choices(self):
        q = self.question()
        image = q["image"]
        self.assertTrue(image["available"])
        self.assertEqual(image["path"], "~/Projects/site/shots/home.png")
        self.assertEqual(image["name"], "home.png")
        self.assertNotIn(str(self.h.home), str(image["url"]) + str(image["thumbnail"]))
        self.assertEqual(q["options"][:2], ["1. Yes", "2. Yes, allow reading from shots/ during this session"])
        file, mime, size = self.c.question_image(image["id"])
        with file:
            self.assertEqual((file.read(), mime, size), (PNG, "image/png", len(PNG)))
        file, mime, _ = self.c.question_image(image["id"], thumbnail=True)
        file.close()
        self.assertIn(mime, ("image/jpeg", "image/png"))

    def test_outside_path_shows_the_path_and_a_reason(self):
        outside = self.h.file("Downloads/shot.png")
        self.status("idle")
        self.herdr.screens["w1:p1"] = dialog(outside)
        self.status("blocked")
        image = self.question()["image"]
        self.assertFalse(image["available"])
        self.assertEqual(image["reason"], REASONS["outside"])
        self.assertIsNone(image["id"])
        self.assertEqual(image["path"], "~/Downloads/shot.png")

    def test_unknown_token_closed_question_and_swapped_file_are_refused(self):
        token = self.question()["image"]["id"]
        with self.assertRaises(ValueError):
            self.c.question_image("0" * 32)
        # The file is swapped for a symlink out of the project folder after the check.
        self.shot.unlink()
        self.shot.symlink_to(self.h.file("Downloads/other.png"))
        with self.assertRaises(ValueError):
            self.c.question_image(token)
        self.shot.unlink()
        self.shot.write_bytes(PNG)
        os.utime(self.shot, ns=(1, 1))
        with self.assertRaises(ValueError):   # a changed file gets a new token
            self.c.question_image(token)
        token = self.question()["image"]["id"]
        self.status("idle")
        self.assertFalse([q for q in self.c.view(local=False)["questions"] if q["identity"] == "claude:ask"])
        with self.assertRaises(ValueError):
            self.c.question_image(token)


class PhoneRoute(unittest.TestCase):
    """Same login rule as every other API route; no path in the URL."""
    from tests.test_api import PhoneApi
    request, login = PhoneApi.request, PhoneApi.login

    def setUp(self):
        from herdr_attention.api import PhoneServer, make_handler, serve
        from tests.test_api import PASSPHRASE
        from tests.helpers import FakeKeyring
        blocked_rig(self)
        self.c.keyring = FakeKeyring({"dashboard-passphrase": PASSPHRASE})
        self.c.settings.set_override("phone.cookie_secure", False)
        self.server = PhoneServer(("127.0.0.1", 0), make_handler(self.c, local=False))
        self.port = self.server.server_address[1]
        serve(self.server)
        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()

    def get(self, path, cookie=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, dict(response.getheaders()), raw

    def test_image_needs_login_and_a_known_token(self):
        image = open_question(self.c)["image"]
        self.assertEqual(self.get(image["url"])[0], 401)
        cookie, _ = self.login()
        status, headers, raw = self.get(image["url"], cookie)
        self.assertEqual((status, raw), (200, PNG))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertEqual(self.get(image["thumbnail"], cookie)[0], 200)
        for path in ("/api/question-images/" + "0" * 32 + "/file", "/api/question-images/..%2Fetc/file",
                     "/api/question-images/" + "A" * 32 + "/file", "/api/question-images/x/file"):
            self.assertEqual(self.get(path, cookie)[0], 404, path)
        self.assertEqual(self.request("POST", image["url"], {}, cookie=cookie)[0], 403)


if __name__ == "__main__":
    unittest.main()
