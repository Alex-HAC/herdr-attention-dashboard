"""Run the real collector installer in an isolated home with no live services."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallRouting(unittest.TestCase):
    def install(self, home):
        commands=home/'commands';commands.mkdir(exist_ok=True)
        for command, body in {
            'systemctl':'printf "%s\\n" "$*" >> "$HOME/service-calls"',
            'sleep':':',
            # Forbidden integrations must never run in collector-only mode.
            'herdr':'exit 88', 'hyprctl':'exit 88', 'omarchy':'exit 88',
        }.items():
            path=commands/command;path.write_text('#!/bin/bash\n'+body+'\n');path.chmod(0o755)
        env={**os.environ,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),
             'XDG_STATE_HOME':str(home/'.local/state'),'PATH':f'{commands}:'+os.environ['PATH']}
        return subprocess.run(['bash',str(ROOT/'scripts/install.sh'),'--collector-only','--skip-checks'],env=env,capture_output=True,text=True,timeout=30)

    def test_existing_routing_survives_and_new_defaults_have_private_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);app=home/'.local/share/herdr-attention/app'
            (app/'config').mkdir(parents=True)
            routing='[accounts.codex-personal]\nprovider="codex"\n\n[[project]]\npath="~/Projects/herdr-attention-dashboard-v0813"\naccounts=["codex-personal"]\n'
            (app/'config/routing.toml').write_text(routing)
            # Database has to survive an online installer backup too.
            import sqlite3
            state=home/'.local/state/herdr-attention/state.db';state.parent.mkdir(parents=True)
            with sqlite3.connect(state) as db:
                db.execute('CREATE TABLE proof(value)');db.execute("INSERT INTO proof VALUES ('preserved')")
            result=self.install(home)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((app/'config/routing.toml').read_text(),routing)
            self.assertEqual((app/'config/routing.toml.dist').read_bytes(),(ROOT/'config/routing.toml').read_bytes())
            backup=home/'.local/share/herdr-attention/backup'
            diff,=backup.glob('routing.toml.*.diff')
            self.assertEqual(diff.stat().st_mode & 0o777,0o600)
            self.assertIn('codex-personal',diff.read_text())
            self.assertNotIn('accounts=[',result.stdout,'routing diff must not be printed')
            copy,=backup.glob('state.db.*')
            with sqlite3.connect(copy) as db:
                self.assertEqual(db.execute('SELECT value FROM proof').fetchone()[0],'preserved')
            calls=(home/'service-calls').read_text().splitlines()
            self.assertEqual([c for c in calls if 'restart' in c],['--user restart herdr-attention.service'])
            self.assertFalse((home/'.config/hypr').exists())

    def test_first_install_copies_default_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);result=self.install(home)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((home/'.local/share/herdr-attention/app/config/routing.toml').read_bytes(),(ROOT/'config/routing.toml').read_bytes())

    def test_newer_install_is_refused_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);bin=home/'.local/bin/herdr-attention';bin.parent.mkdir(parents=True)
            bin.write_text('#!/bin/bash\necho 99.0.0\n');bin.chmod(0o755)
            result=self.install(home)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Installed app is newer',result.stderr)
            self.assertFalse((home/'service-calls').exists())
            self.assertFalse((home/'.local/share/herdr-attention/app').exists())
