"""v0.9.8.8 file viewer: path resolution, the deny list, the size and type limits, the endpoint.

Alex's brief: tap team-registry/docs/working-styles.md in a question or report and read it in the
dashboard; read-only, behind the login and the allow list, only under the watched agents' folders and
~/Projects, ~/Work, ~/Documents; no symlink escape, no .., no dotfiles or secrets; 1 MB; never to
the summary agent.
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_attention import files
from herdr_attention.files import FileError
from herdr_attention.ui_contract import CONTRACT
from tests.test_tailnet_access import NAS, PHONE, Servers

DOC = "# Working styles\n\nHow agents work with **Alex**.\n"
DRAFT = "# Working styles\n\nHow agents work with **Alex**.\n\n## Agents\n\nAsk before merging.\n"


class Home:
    """A fake home: ~/Projects/team-registry (a git repo), ~/other (a repo outside the fixed folders)."""

    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        home = root / "home"
        repo = home / "Projects" / "team-registry"
        (repo / ".git").mkdir(parents=True)
        (repo / "docs").mkdir()
        (repo / "docs" / "working-styles.md").write_text(DOC)
        (repo / "docs" / "working-styles-agents-section.md").write_text(DRAFT)
        (repo / "src").mkdir()
        (repo / ".env.md").write_text("SECRET=1")
        (repo / "credentials.md").write_text("pw")
        (repo / "api-keys.txt").write_text("k")
        (repo / "big.md").write_text("x" * (files.MAX_BYTES + 1))
        (repo / "nul.txt").write_bytes(b"a\x00b")
        (repo / "latin.txt").write_bytes(b"caf\xe9")
        (repo / "logo.png").write_bytes(b"\x89PNG")
        (home / ".ssh").mkdir()
        (home / ".ssh" / "notes.md").write_text("private")
        (home / ".claude").mkdir()
        (home / ".claude" / "CLAUDE.md").write_text("private")
        (home / "notes.md").write_text("home root")
        (root / "outside.md").write_text("outside")
        (repo / "docs" / "escape.md").symlink_to(home / ".ssh" / "notes.md")
        (repo / "docs" / "out.md").symlink_to(root / "outside.md")
        (repo / "docs" / "inside.md").symlink_to(repo / "docs" / "working-styles.md")
        other = home / "other"
        (other / ".git").mkdir(parents=True)
        (other / "agent").mkdir()
        (other / "README.md").write_text("other readme")
        (home / "Work").mkdir()
        self.home, self.repo, self.other, self.root = home, repo, other, root
        return home


class Resolution(Home, unittest.TestCase):
    def setUp(self):
        self.make()

    def read(self, raw, cwd=None, cwds=()):
        return files.read(raw, str(cwd) if cwd else None, [str(c) for c in cwds], self.home)

    def status(self, raw, cwd=None, cwds=()):
        with self.assertRaises(FileError) as caught:
            self.read(raw, cwd, cwds)
        return caught.exception.status

    def test_a_relative_path_starts_from_the_agents_folder_and_repo(self):
        got = self.read("docs/working-styles.md", cwd=self.repo)
        self.assertEqual(got["path"], str(self.repo / "docs" / "working-styles.md"))
        self.assertEqual(got["shown"], "~/Projects/team-registry/docs/working-styles.md")
        self.assertEqual((got["text"], got["kind"], got["name"]), (DOC, "markdown", "working-styles.md"))
        self.assertAlmostEqual(got["mtime"], (self.repo / "docs" / "working-styles.md").stat().st_mtime)
        # From a sub folder of the repo: found from the repo root.
        self.assertEqual(self.read("docs/working-styles.md", cwd=self.repo / "src")["text"], DOC)
        # A path as agents write it from the parent folder: found below ~/Projects.
        self.assertEqual(self.read("team-registry/docs/working-styles.md")["text"], DOC)

    def test_home_and_absolute_paths(self):
        self.assertEqual(self.read("~/Projects/team-registry/docs/working-styles.md")["text"], DOC)
        self.assertEqual(self.read(str(self.repo / "docs" / "working-styles.md"))["text"], DOC)
        self.assertEqual(self.read("./docs/working-styles.md", cwd=self.repo)["text"], DOC)

    def test_a_watched_agents_repo_outside_projects_is_allowed_home_itself_is_not(self):
        self.assertEqual(self.status("~/other/README.md"), 403)
        self.assertEqual(self.read("README.md", cwd=self.other / "agent")["text"], "other readme")
        self.assertEqual(self.read("~/other/README.md", cwds=[self.other / "agent"])["text"], "other readme")
        # An agent sitting in home would open all of home: it adds no folder.
        self.assertNotIn(self.home, files.roots([str(self.home)], self.home))
        self.assertEqual(self.status("notes.md", cwd=self.home), 404, "looked for only in the allowed folders")
        self.assertEqual(self.status("~/notes.md", cwds=[self.home]), 403)

    def test_missing_files_are_404(self):
        self.assertEqual(self.status("docs/nope.md", cwd=self.repo), 404)
        self.assertEqual(self.status("~/Projects/nope.md"), 404)

    def test_the_deny_list(self):
        cases = {
            "../outside.md": 403, "docs/../../outside.md": 403, "~/.ssh/notes.md": 403, "~/.claude/CLAUDE.md": 403,
            ".env.md": 403, "credentials.md": 403, "api-keys.txt": 403, "docs/escape.md": 403, "docs/out.md": 403,
            str(self.root / "outside.md"): 403, "/etc/hosts.txt": 403, "~root/x.md": 403,
            "big.md": 413, "nul.txt": 415, "latin.txt": 415, "logo.png": 415, "docs": 415,
            "http://example.com/a.md": 400, "": 400, "a\\b.md": 400,
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.status(raw, cwd=self.repo), want)

    def test_a_symlink_inside_the_folders_is_fine(self):
        got = self.read("docs/inside.md", cwd=self.repo)
        self.assertEqual(got["path"], str(self.repo / "docs" / "working-styles.md"))

    def test_outside_paths_are_refused_before_looking(self):
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("looked")) as looked:
            with self.assertRaises(FileError) as caught:
                files.resolve(str(self.root / "secret-plans.md"), [], [self.repo], self.home)
        self.assertEqual(caught.exception.status, 403)
        looked.assert_not_called()


class Diff(unittest.TestCase):
    def test_original_to_draft(self):
        d = files.diff(DOC, DRAFT)
        self.assertEqual((d["added"], d["removed"], d["same"]), (4, 0, False))
        self.assertEqual([r["text"] for r in d["rows"] if r["op"] == "add"], ["", "## Agents", "", "Ask before merging."])
        self.assertTrue(files.diff(DOC, DOC)["same"])

    def test_long_unchanged_runs_fold(self):
        a = "\n".join(f"line {i}" for i in range(100))
        b = a.replace("line 50", "line fifty")
        rows = files.diff(a, b)["rows"]
        self.assertLess(len(rows), 20)
        self.assertEqual([r["op"] for r in rows if r["op"] != "same"], ["skip", "del", "add", "skip"])


class Chips(unittest.TestCase):
    """The pattern the page uses to make paths tappable (ui_contract files.pattern)."""

    def found(self, text):
        return [m.group(1) for m in re.finditer(CONTRACT["files"]["pattern"], text, re.I)]

    def test_paths_in_agent_text(self):
        text = ("See team-registry/docs/working-styles.md and work/next-20261119/gates-review/docs/team-registry/docs/"
                "working-styles-agents-section.md, also ~/Projects/x/NOTES.txt (or /home/a/b.markdown). README.md.")
        self.assertEqual(self.found(text), [
            "team-registry/docs/working-styles.md",
            "work/next-20261119/gates-review/docs/team-registry/docs/working-styles-agents-section.md",
            "~/Projects/x/NOTES.txt", "/home/a/b.markdown", "README.md"])

    def test_not_urls_hidden_folders_or_other_types(self):
        for text in ("https://github.com/a/b/README.md", "~/.claude/CLAUDE.md", "app.py", "notes.md.bak", "a.mdx2",
                     "x@y.com", "v1.2.md5"):
            with self.subTest(text=text):
                self.assertEqual(self.found(text), [])


class Endpoint(Home, Servers):
    def setUp(self):
        super().setUp()
        self.make()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        agent = next(iter(self.collector.engine.agents.values()))
        agent["cwd"] = str(self.repo)
        self.identity = agent["identity"]

    def get(self, path, ip=PHONE, cookie=None, server=None):
        headers = {"X-Forwarded-For": ip, **({"Cookie": cookie} if cookie else {})}
        status, hdrs, raw = self.request(server or self.phone, "GET", path, headers=headers)
        return status, hdrs, raw

    def test_behind_the_login_read_only_and_not_stored(self):
        path = f"/api/file?identity={self.identity}&path=docs/working-styles-agents-section.md&against=docs/working-styles.md"
        self.assertEqual(self.get(path)[0], 401)
        cookie, csrf = self.login(PHONE)
        status, hdrs, raw = self.get(path, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(hdrs.get("Cache-Control"), "no-store")
        data = json.loads(raw)
        self.assertEqual(data["text"], DRAFT)
        self.assertEqual(data["against"]["name"], "working-styles.md")
        self.assertEqual(data["diff"]["added"], 4)
        status, _, _ = self.request(self.phone, "POST", path, {}, {"X-Forwarded-For": PHONE, "Cookie": cookie, "X-CSRF-Token": csrf})
        self.assertEqual(status, 405)
        # The text is in no view, so no summary request or push can carry it.
        self.assertNotIn("Ask before merging", json.dumps(self.collector.view(local=False)))

    def test_refusals_come_back_as_plain_errors(self):
        cookie, _ = self.login(PHONE)
        for raw, want in (("~/.ssh/notes.md", 403), ("../outside.md", 403), ("docs/nope.md", 404), ("big.md", 413)):
            status, _, body = self.get(f"/api/file?identity={self.identity}&path={raw}", cookie=cookie)
            self.assertEqual(status, want, raw)
            self.assertNotIn(b"private", body)

    def test_the_allow_list_applies(self):
        self.assertEqual(self.desk("POST", {"entries": ["pixel-10a"], "enabled": True})[0], 200)
        cookie, _ = self.login(PHONE)
        status, _, raw = self.get(f"/api/file?identity={self.identity}&path=docs/working-styles.md", ip=NAS, cookie=cookie)
        self.assertEqual((status, raw), (403, b"forbidden\n"))

    def test_the_summariser_never_sees_files(self):
        src = (Path(files.__file__).parent / "summarize.py").read_text()
        self.assertNotIn("files", re.findall(r"^(?:from|import) [.\w]+(?: import [\w, ]+)?", src, re.M).__str__())
        self.assertNotIn("read_file", src)


if __name__ == "__main__":
    unittest.main()
