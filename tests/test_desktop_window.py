"""Desktop capability boundaries, real HTTP listeners, and narrow focus writes."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from herdr_attention.api import PhoneServer, make_handler, serve
from herdr_attention.config import Settings
from herdr_attention.desktop import herdr_window, overview_main, raise_herdr
from herdr_attention.desktop_install import install
from herdr_attention.focus import FocusService, FocusTarget, HerdrFocus
from herdr_attention.herdr import HerdrClient, HerdrError, READ_METHODS
from herdr_attention.ui_contract import API, CONTRACT, FEATURES, desktop_url, hash_path
from tests.test_api import make_collector


class DesktopApi(unittest.TestCase):
    def setUp(self):
        self.collector = make_collector()
        self.addCleanup(self.collector.store.close)
        self.collector.focus_agent = Mock(return_value={'ok': True, 'hash': hash_path('conversation', 'claude:a')})
        self.phone = self.server(False)
        self.desktop = self.server(True)

    def server(self, desktop):
        server = PhoneServer(('127.0.0.1', 0), make_handler(self.collector, local=False, desktop=desktop))
        serve(server)
        def close():
            server.stopping.set()
            server.shutdown()
            server.server_close()
        self.addCleanup(close)
        return server

    def request(self, server, path, method='GET', body=None, headers=None):
        host, port = server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=3)
        try:
            raw = json.dumps(body) if body is not None else None
            conn.request(method, path, raw, headers or {})
            response = conn.getresponse()
            data = response.read()
            return response.status, json.loads(data) if response.getheader('Content-Type', '').startswith('application/json') else data
        finally:
            conn.close()

    def headers(self, server):
        return {'Origin': f'http://127.0.0.1:{server.server_address[1]}',
                'Content-Type': 'application/json', 'X-CSRF-Token': server.csrf}

    def test_desktop_focus_needs_csrf_and_same_origin(self):
        self.assertEqual(self.request(self.desktop, API['session'])[0], 200)
        for headers in ({}, {'Origin': 'https://attacker.example'},
                        {**self.headers(self.desktop), 'X-CSRF-Token': 'wrong'},
                        {**self.headers(self.desktop), 'Origin': 'https://attacker.example'},
                        {**self.headers(self.desktop), 'Host': 'attacker.example'}):
            self.assertEqual(self.request(self.desktop, API['focus'], 'POST', {'identity': 'claude:a'}, headers)[0], 403)
        self.collector.focus_agent.assert_not_called()
        status, data = self.request(self.desktop, API['focus'], 'POST', {'identity': 'claude:a'}, self.headers(self.desktop))
        self.assertEqual(status, 200)
        self.assertEqual(data['hash'], hash_path('conversation', 'claude:a'))
        self.collector.focus_agent.assert_called_once_with('claude:a')

    def test_phone_tailnet_and_forwarded_headers_always_404(self):
        for host in ('127.0.0.1:8765', 'laptop.example.ts.net', '127.0.0.1:8766'):
            for path in (API['focus'], API['focus'] + '?desktop=true&local=1', API['desktop_navigation']):
                status, _ = self.request(self.phone, path, 'POST', {'identity': 'claude:a'}, {
                    **self.headers(self.desktop), 'Host': host,
                    'X-Forwarded-For': '127.0.0.1', 'X-Forwarded-Host': '127.0.0.1:8766',
                    'X-Forwarded-Proto': 'http', 'Tailscale-User-Login': 'alex@example.test',
                    'Tailscale-User-Name': 'Alex'})
                self.assertEqual(status, 404)
        self.collector.focus_agent.assert_not_called()

    def test_wrapper_routes_come_from_the_contract(self):
        for name in ('quotas', 'settings'):
            target = hash_path(name)
            status, result = self.request(self.desktop, API['desktop_navigation'], 'POST', {'hash': target}, self.headers(self.desktop))
            self.assertEqual((status, result['hash']), (200, target))
        for bad in (hash_path('agent', 'claude:a'), hash_path('home'), hash_path('notification', 'x')):
            self.assertEqual(self.request(self.desktop, API['desktop_navigation'], 'POST', {'hash': bad}, self.headers(self.desktop))[0], 400)
        self.collector.focus_agent.assert_not_called()

    def test_visuals_have_no_second_focus_route(self):
        # The visual viewer reuses the shared focus action; no peer-address-gated copy.
        old = API['visuals'] + '/' + 'a' * 32 + '/open'
        status, _ = self.request(self.desktop, old, 'POST', {'identity': 'claude:a'}, self.headers(self.desktop))
        self.assertEqual(status, 404)
        self.collector.focus_agent.assert_not_called()

    def test_features_are_bound_to_server_not_headers(self):
        _, desktop = self.request(self.desktop, API['view'])
        self.assertTrue(desktop['features'][FEATURES['desktop_focus']])
        status, _ = self.request(self.desktop, '/')
        self.assertEqual(status, 200)
        _, login = self.request(self.phone, '/login', 'POST', {'passphrase': 'correct horse battery'},
                                {'Origin': 'http://phone.test', 'Host': 'phone.test', 'Content-Type': 'application/json'})
        # Use a real authenticated phone session from the store, independent
        # of the desktop token (the login above also exercises phone auth).
        import hashlib, time
        token = 'phone-test-session'
        self.collector.store.add_session(hashlib.sha256(token.encode()).hexdigest(), 'phone-csrf', time.time()+100)
        _, phone = self.request(self.phone, API['view'], headers={'Cookie': 'hat_session='+token,
                               'X-Forwarded-Host': '127.0.0.1:8766'})
        self.assertFalse(phone['features'][FEATURES['desktop_focus']])
        self.assertNotIn('desktop', phone)
        self.assertNotEqual(login['csrf'], self.desktop.csrf)

    def test_missing_gone_and_unwatched_targets_return_404(self):
        self.collector.focus_agent.side_effect = LookupError()
        self.assertEqual(self.request(self.desktop, API['focus'], 'POST', {'identity': 'missing'}, self.headers(self.desktop))[0], 404)
        self.assertEqual(self.request(self.desktop, API['focus'], 'POST', {}, self.headers(self.desktop))[0], 400)

    def test_notification_navigation_is_desktop_only_and_requires_csrf(self):
        target = hash_path('notification', 42)
        self.assertEqual(self.request(self.desktop, API['desktop_navigation'], 'POST', {'hash': target})[0], 403)
        status, result = self.request(self.desktop, API['desktop_navigation'], 'POST', {'hash': target}, self.headers(self.desktop))
        self.assertEqual(status, 200)
        self.assertEqual(result, {'hash': target, 'sequence': 1})
        self.assertEqual(self.request(self.desktop, API['desktop_navigation'])[1], result)
        self.assertEqual(self.request(self.desktop, API['desktop_navigation'], 'POST', {'hash': '#/evil'}, self.headers(self.desktop))[0], 400)
        self.assertEqual(self.request(self.phone, API['desktop_navigation'])[0], 404)
        self.collector.focus_agent.assert_not_called()

    def test_wrapper_navigates_existing_window_without_focusing_a_pane(self):
        conf = {'desktop': {'port': self.desktop.server_address[1]}}
        with patch('herdr_attention.desktop.load_file', return_value=conf), \
             patch('herdr_attention.desktop.clients', return_value=[{'title': CONTRACT['desktop']['title'], 'address': 'existing'}]), \
             patch('herdr_attention.desktop.active_workspace', return_value={'id': 1, 'name': '1'}), \
             patch('herdr_attention.desktop.focus_window') as focus:
            overview_main(['--notification', '42'])
        focus.assert_called_once_with('existing')
        self.assertEqual(self.desktop.navigation['hash'], hash_path('notification', 42))
        self.collector.focus_agent.assert_not_called()

    def test_public_bind_is_rejected_before_listening(self):
        with self.assertRaises(ValueError):
            PhoneServer(('0.0.0.0', 0), make_handler(self.collector, local=True, desktop=True))
        with self.assertRaises(ValueError):
            desktop_url(Settings({'desktop': {'bind': '0.0.0.0'}}))


class Focus(unittest.TestCase):
    def setUp(self):
        self.collector = make_collector()
        self.addCleanup(self.collector.store.close)
        self.service = FocusService(self.collector.engine, '/tmp/test-herdr.sock')

    def test_identity_mapping_uses_engine_for_all_identity_forms(self):
        for identity in ('claude:a', 'codex:uuid', 'term:terminal', 'claude:a@terminal'):
            with patch.object(self.collector.engine, 'agent_by_identity', return_value={
                'identity': identity, 'present': True, 'pane_id': 'w2:p8', 'name': 'worker', 'tab_id': 'w2:t3', 'workspace_id': 'w2'}):
                target = FocusTarget.resolve(self.collector.engine, identity)
                self.assertEqual(target.pane_id, 'w2:p8')
                self.assertEqual(target.name, 'worker')
                self.assertEqual([method for method, _ in target.fallback_steps()],
                                 ['workspace.focus', 'tab.focus', 'pane.focus'])

    def test_focus_prefers_cli_and_raises_window_after_success(self):
        with patch('herdr_attention.focus.subprocess.run') as run, patch('herdr_attention.focus.raise_herdr', return_value='focused') as raise_window:
            result = self.service.focus('claude:a')
        self.assertEqual(run.call_args.args[0], ['herdr', 'agent', 'focus', 'w1:p1'])
        self.assertEqual(run.call_args.kwargs['env']['HERDR_SOCKET_PATH'], '/tmp/test-herdr.sock')
        raise_window.assert_called_once_with()
        self.assertEqual(result['hash'], hash_path('conversation', 'claude:a'))

    def test_fallback_order_and_focus_only_allowlist(self):
        with patch('herdr_attention.focus.subprocess.run', side_effect=FileNotFoundError()), \
             patch.object(HerdrFocus, 'call') as call, patch('herdr_attention.focus.raise_herdr', return_value='focused'):
            self.service.focus('claude:a')
        self.assertEqual([c.args[0] for c in call.call_args_list], ['workspace.focus', 'tab.focus', 'pane.focus'])
        self.assertEqual(HerdrClient.ALLOWED_METHODS, READ_METHODS)
        for method in ('pane.send_keys', 'agent.prompt', 'agent.focus', 'agent.read'):
            with self.assertRaises(HerdrError):
                HerdrFocus('/tmp/no-socket').call(method)
        self.assertFalse(READ_METHODS & HerdrFocus.ALLOWED_METHODS)

    def test_unwatched_gone_and_unknown_never_focus(self):
        with patch('herdr_attention.focus.subprocess.run') as run:
            for identity in ('claude:b', 'missing'):
                with self.assertRaises(LookupError): self.service.focus(identity)
            self.collector.engine.agents['claude:a']['present'] = False
            with self.assertRaises(LookupError): self.service.focus('claude:a')
        run.assert_not_called()

    def test_failed_socket_focus_never_raises_window(self):
        with patch('herdr_attention.focus.subprocess.run', side_effect=FileNotFoundError()), \
             patch.object(HerdrFocus, 'call', side_effect=HerdrError('gone')), patch('herdr_attention.focus.raise_herdr') as raise_window:
            with self.assertRaises(HerdrError): self.service.focus('claude:a')
            raise_window.assert_not_called()

    def test_window_matching_requires_herdr_child_pid_in_ghostty(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            for pid, parent, comm, args in ((30, 20, 'herdr', b'herdr\0'), (20, 10, 'bash', b'bash\0'),
                                            (10, 1, 'ghostty', b'ghostty\0'), (40, 1, 'herdr', b'herdr\0server\0')):
                path = proc/str(pid); path.mkdir()
                (path/'comm').write_text(comm)
                (path/'cmdline').write_bytes(args)
                (path/'status').write_text(f'PPid:\t{parent}\n')
            windows = [{'class': 'chromium', 'title': 'Herdr', 'pid': 30, 'address': 'bad'},
                       {'class': 'com.mitchellh.ghostty', 'pid': 10, 'address': 'correct'}]
            self.assertEqual(herdr_window(windows, proc)['address'], 'correct')
            self.assertIsNone(herdr_window(windows[:1], proc))
        with patch('herdr_attention.desktop.clients', return_value=[]), patch('herdr_attention.desktop.run') as run:
            self.assertEqual(raise_herdr(), 'launched')
            run.assert_called_once_with(['omarchy-launch-terminal-herdr'])

    def test_wrapper_reads_custom_port_and_title_fallback(self):
        ws = patch('herdr_attention.desktop.active_workspace', return_value={'id': 1, 'name': '1'})
        with tempfile.TemporaryDirectory() as tmp, patch('herdr_attention.desktop.LAUNCH_STAMP', Path(tmp) / 'stamp'):
            with patch('herdr_attention.desktop.load_file', return_value={'desktop': {'port': 9007}}), ws, \
                 patch('herdr_attention.desktop.clients', return_value=[{'title': CONTRACT['desktop']['title'], 'class': 'chromium', 'address': 'existing'}]), \
                 patch('herdr_attention.desktop.focus_window') as focus, patch('herdr_attention.desktop.subprocess.Popen') as launch:
                self.assertEqual(overview_main([]), 0)
                focus.assert_called_once_with('existing')
                launch.assert_not_called()
            with patch('herdr_attention.desktop.load_file', return_value={'desktop': {'port': 9007}}), ws, \
                 patch('herdr_attention.desktop.clients', return_value=[]), patch('herdr_attention.desktop.subprocess.Popen') as launch:
                overview_main([])
                # v0.9.8.4: no --class (a running Chromium ignores it; a starting one gives it to every window).
                self.assertEqual(launch.call_args.args[0], ['omarchy-launch-webapp', 'http://127.0.0.1:9007/'])

    def test_installer_is_idempotent_and_keeps_work_search(self):
        with tempfile.TemporaryDirectory() as tmp, patch('herdr_attention.desktop_install.subprocess.run', side_effect=FileNotFoundError()), \
             patch('herdr_attention.config.load_file', return_value={}):
            base = Path(tmp); bindings = base/'bindings.lua'
            old = 'o.bind("SUPER + SHIFT + S", "Work search", "work-search")\n'
            bindings.write_text(old)
            for _ in range(2): install(base/'app', base/'bin', bindings, base/'backup')
            content = bindings.read_text()
            self.assertIn(old.strip(), content)
            self.assertEqual(content.count(CONTRACT['desktop']['hotkey']), 1)
            self.assertNotIn('8766', (base/'bin/herdr-attention-overview').read_text())
            from herdr_attention.desktop_install import remove
            remove(bindings, base/'backup')
            self.assertEqual(bindings.read_text(), old)
