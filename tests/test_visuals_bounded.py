"""The Latest visuals scan stays bounded: time budget, caps, cache, and no shared lock.

Regression for 16 Nov 2026: one GET /api/visuals ran over two minutes at full CPU and
starved /api/view and /api/health. No live agent calls.
"""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from herdr_attention import visuals
from herdr_attention.visuals import Budget, Visuals, path_candidates
from tests.helpers import settings

IDENTITY='claude:12345678-1234-5678-1234-123456789abc'
PATTERN=re.compile(r'\.(?:png|jpg|webp)\b',re.I)


class Candidates(unittest.TestCase):
    def test_finds_plain_spaced_and_absolute_names(self):
        found=path_candidates('See shots/my file.png, /abs/dir/x.jpg and plain.webp.',PATTERN)
        self.assertIn('shots/my file.png',found);self.assertIn('/abs/dir/x.jpg',found);self.assertIn('plain.webp',found)

    def test_hostile_megabyte_strings_take_linear_time(self):
        # Each of these made the old nested-quantifier patterns backtrack for minutes.
        for text in ('a/'*500000,'A'*1000000,'a.'*500000,('x'*300+'.png')*3000,'a b-'*250000+'.pn'):
            started=time.monotonic();path_candidates(text,PATTERN)
            self.assertLess(time.monotonic()-started,2.0,text[:12])


class Bounded(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        self.trace=Path(self.tmp.name)/'trace.jsonl'
        self.locator=SimpleNamespace(find=lambda *parts:self.trace if self.trace.exists() else None)
        self.library=Visuals(settings(),self.locator,Path(self.tmp.name)/'cache')
        self.view={'agents':[{'identity':IDENTITY,'cwd':str(self.root),'name':'builder','workspace_id':'w1'}],'workspaces':[{'id':'w1'}]}

    def test_complete_scan_is_not_partial(self):
        (self.root/'screen.png').write_text('fixture')
        result=self.library.scan(self.view)
        self.assertEqual([i['name'] for i in result['items']],['screen.png'])
        self.assertFalse(result['partial']);self.assertEqual(result['partial_reason'],'')

    def test_trace_with_huge_hostile_lines_finishes_fast(self):
        (self.root/'screen.png').write_text('fixture')
        lines=[json.dumps({'text':'a/'*200000}),json.dumps({'image':'QUJD'*100000}),json.dumps({'text':'done screen.png'})]
        self.trace.write_text('\n'.join(lines)+'\n')
        started=time.monotonic();result=self.library.scan(self.view)
        self.assertLess(time.monotonic()-started,3.0)
        self.assertEqual(next(i for i in result['items'] if i['name']=='screen.png')['source'],'trace')

    def test_heavy_folders_are_skipped_even_when_ignore_list_is_empty(self):
        self.library.settings.get=lambda key,default=None:[] if key=='visuals.ignore_globs' else default
        for folder in ('node_modules/pkg','.venv/lib','target','shots'):
            (self.root/folder).mkdir(parents=True);(self.root/folder/'a.png').write_text('fixture')
        found=self.library._disk_paths(self.root,0,[])
        self.assertEqual(found,['shots/a.png'])

    def test_file_cap_returns_partial_with_reason(self):
        (self.root/'screen.png').write_text('fixture')
        with patch.object(visuals,'MAX_WALK_FILES',50):
            for index in range(80):(self.root/f'note{index}.txt').write_text('x')
            result=self.library.scan(self.view)
        self.assertTrue(result['partial']);self.assertEqual(result['partial_reason'],'file limit reached')

    def test_folder_cap_stops_the_walk(self):
        for index in range(12):(self.root/f'd{index:02}').mkdir()
        budget=Budget()
        with patch.object(visuals,'MAX_WALK_DIRS',5):self.library._disk_paths(self.root,0,[],budget)
        self.assertEqual(budget.reason,'folder limit reached')

    def test_spent_time_budget_returns_partial_quickly(self):
        (self.root/'screen.png').write_text('fixture')
        self.library.budget_seconds=lambda:0.0
        started=time.monotonic();result=self.library.scan(self.view)
        self.assertLess(time.monotonic()-started,2.0)
        self.assertTrue(result['partial']);self.assertEqual(result['partial_reason'],'time budget reached')

    def test_result_is_cached_and_refresh_cannot_hammer(self):
        (self.root/'screen.png').write_text('fixture')
        with patch.object(self.library,'_disk_paths',wraps=self.library._disk_paths) as walk:
            self.library.scan(self.view);self.library.scan(self.view);self.library.scan(self.view,refresh=True)
            self.assertEqual(walk.call_count,1)

    def test_second_scan_does_not_queue_behind_a_running_one(self):
        self.library.budget_seconds=lambda:0.2
        self.assertTrue(self.library.scan_lock.acquire())
        try:
            started=time.monotonic();result=self.library.scan(self.view)
        finally:self.library.scan_lock.release()
        self.assertLess(time.monotonic()-started,2.0)
        self.assertEqual(result,{'items':[],'partial':True,'partial_reason':'another scan is still running'})

    def test_git_lookup_does_not_hold_the_visuals_lock(self):
        free=[]
        def fake_run(*args,**kwargs):
            def probe():
                got=self.library.lock.acquire(timeout=1);free.append(got)
                if got:self.library.lock.release()
            thread=threading.Thread(target=probe);thread.start();thread.join()
            return SimpleNamespace(returncode=1,stdout='')
        with patch.object(visuals.subprocess,'run',fake_run):self.library.roots(self.view)
        self.assertEqual(free,[True])

    def test_reading_a_file_does_not_run_git(self):
        # v0.9.9.1: tiles used to stall because every thumbnail re-ran git. Authorization
        # now uses watched cwds already in the view.
        (self.root/'screen.png').write_text('fixture');item,=self.library.scan(self.view)['items']
        self.library.root_cache.clear();calls=[]
        def fake_run(*args,**kwargs):
            calls.append(args);return SimpleNamespace(returncode=1,stdout='')
        with patch.object(visuals.subprocess,'run',fake_run):
            file,_,_=self.library.read(self.view,item['id']);file.close()
            file,_,_=self.library.read(self.view,item['id'],True);file.close()
        git=[c for c in calls if c and c[0] and c[0][0]=='git']
        self.assertEqual(git,[])

    def test_scan_leaves_other_threads_responsive(self):
        # Stand-in for /api/view and /api/health: a ticking thread must keep running during a scan.
        (self.root/'screen.png').write_text('fixture')
        self.trace.write_text('\n'.join(json.dumps({'text':'a/'*100000+' b c.png'}) for _ in range(8))+'\n')
        gaps=[];stop=threading.Event()
        def tick():
            last=time.monotonic()
            while not stop.is_set():
                time.sleep(0.01);now=time.monotonic();gaps.append(now-last);last=now
        thread=threading.Thread(target=tick);thread.start()
        try:self.library.scan(self.view)
        finally:stop.set();thread.join()
        self.assertLess(max(gaps),1.5)

if __name__=='__main__':unittest.main()
