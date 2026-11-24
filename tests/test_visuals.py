"""Generic media discovery and authenticated capability reads. No live agent calls."""
import http.client
import json
import os
import time
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from herdr_attention.visuals import Visuals, open_beneath, collapse_identical, placeholder_jpeg_bytes, is_valid_thumbnail
from tests.helpers import settings
from tests.test_api import make_collector, PASSPHRASE
from herdr_attention.api import PhoneServer,make_handler,serve
from tests.web_fixture import page_fixture
from tests.web_harness import BROWSER,run
from tests.test_typing_survives_refresh import reading

class Detection(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'work';self.root.mkdir()
        self.trace=Path(self.tmp.name)/'trace.jsonl'
        self.locator=SimpleNamespace(find=lambda *parts:self.trace if self.trace.exists() else None)
        self.library=Visuals(settings(),self.locator,Path(self.tmp.name)/'cache')
        self.view={'agents':[{'identity':'claude:12345678-1234-5678-1234-123456789abc','cwd':str(self.root),'name':'builder','workspace_id':'w1'}], 'workspaces':[{'id':'w1'}]}

    def test_trace_tool_arguments_git_and_mtime_use_same_detector(self):
        for name in ('screen.png','video.mov','older.svg','space image.webp','other.txt'):
            (self.root/name).write_text('fixture')
        os.utime(self.root/'older.svg',(1,1))
        (self.root/'node_modules').mkdir();(self.root/'node_modules/noise.png').write_text('fixture')
        self.trace.write_text(json.dumps({'message':{'tool_calls':[{'arguments':json.dumps({'path':'older.svg'})}],
                        'text':'Look at `space image.webp` and screen.png'}})+'\n')
        items=self.library.discover(self.view)
        names={i['name'] for i in items}
        self.assertEqual(names,{'screen.png','video.mov','older.svg','space image.webp'})
        self.assertEqual(next(i for i in items if i['name']=='older.svg')['source'],'trace')
        self.assertTrue(next(i for i in items if i['name']=='video.mov')['video'])
        self.assertEqual(items,sorted(items,key=lambda i:i['at'],reverse=True))
        file,_,_=self.library.read(self.view,items[0]['id']);file.close()
        with self.assertRaises(ValueError):self.library.read({'agents':[]},items[0]['id'])

    def test_short_home_path_and_worktree_subdirectory_keep_recent_references(self):
        import subprocess
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        (self.root/'src').mkdir()
        (self.root/'older.svg').write_text('<svg/>');os.utime(self.root/'older.svg',(1,1))
        (self.root/'src/local.png').write_text('fixture');os.utime(self.root/'src/local.png',(1,1))
        self.trace.write_text(json.dumps({'text':'older.svg local.png'})+'\n')
        self.view['agents'][0]['cwd']='~/work/src'
        with patch.dict(os.environ,{'HOME':self.tmp.name}):
            self.assertEqual(self.library.roots(self.view)[self.view['agents'][0]['identity']],self.root)
            items=self.library.discover(self.view)
            self.assertEqual({i['relative_path'] for i in items},{'older.svg','src/local.png'})
            for item in items:
                file,_,_=self.library.read(self.view,item['id']);file.close()

    def test_traversal_symlink_and_post_discovery_swap_are_refused(self):
        outside=Path(self.tmp.name)/'secret.png';outside.write_text('private')
        (self.root/'escape.png').symlink_to(outside)
        (self.root/'dir').symlink_to(outside.parent,target_is_directory=True)
        (self.root/'safe.png').write_text('fixture')
        self.trace.write_text(json.dumps({'text':f'{outside} ../secret.png dir/secret.png escape.png'})+'\n')
        items=self.library.discover(self.view)
        self.assertEqual([i['name'] for i in items],['safe.png'])
        (self.root/'safe.png').unlink();(self.root/'safe.png').symlink_to(outside)
        with self.assertRaises(OSError):self.library.read(self.view,items[0]['id'])
        with self.assertRaises(ValueError):open_beneath(self.root,Path('../secret.png'))
        with self.assertRaises(OSError):open_beneath(self.root,Path('dir/secret.png'))
        with self.assertRaises(ValueError):self.library.read(self.view,'../../secret.png')
        alias=Path(self.tmp.name)/'alias';alias.symlink_to(self.root.parent,target_is_directory=True)
        with self.assertRaises(OSError):open_beneath(alias/'work',Path('safe.png'))

    def test_cached_thumbnail_uses_checked_descriptor_and_no_network(self):
        (self.root/'screen.png').write_text('fixture');item,=self.library.discover(self.view)
        preview=placeholder_jpeg_bytes()
        def ffmpeg(command,**kwargs):
            self.assertIn('file,pipe',command);self.assertTrue(kwargs['pass_fds'])
            Path(command[-1]).write_bytes(preview)
            return SimpleNamespace(returncode=0)
        with patch('herdr_attention.visuals.subprocess.run',side_effect=ffmpeg) as runner:
            for _ in range(2):
                file,mime,size=self.library.read(self.view,item['id'],True)
                with file:self.assertEqual(file.read(),preview)
                self.assertIn(mime,('image/jpeg','image/webp'));self.assertEqual(size,len(preview))
        self.assertEqual(runner.call_count,1)
        self.assertIn('scale=320:320:force_original_aspect_ratio=decrease',runner.call_args[0][0])

    def test_invalid_cached_thumbnail_is_regenerated(self):
        (self.root/'screen.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'not-a-real-png-but-source')
        item,=self.library.discover(self.view)
        mtime=int((self.root/'screen.png').stat().st_mtime_ns)
        junk=self.library.cache/f"{item['id']}-{mtime}.webp"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_bytes(b'jpeg-thumb-bytes')
        self.assertFalse(is_valid_thumbnail(junk))
        preview=placeholder_jpeg_bytes()
        def ffmpeg(command,**kwargs):
            Path(command[-1]).write_bytes(preview)
            return SimpleNamespace(returncode=0)
        with patch('herdr_attention.visuals.subprocess.run',side_effect=ffmpeg) as runner:
            file,mime,size=self.library.read(self.view,item['id'],True)
            with file:self.assertEqual(file.read(),preview)
        self.assertEqual(runner.call_count,1)
        self.assertTrue(is_valid_thumbnail(self.library.cache/f"{item['id']}-{mtime}.webp") or
                        is_valid_thumbnail(self.library.cache/f"{item['id']}-{mtime}.jpg"))

    def test_failed_thumb_falls_back_to_the_original_image(self):
        payload=b'\x89PNG\r\n\x1a\n'+b'original-image-bytes'
        (self.root/'screen.png').write_bytes(payload)
        item,=self.library.discover(self.view)
        with patch('herdr_attention.visuals.subprocess.run',return_value=SimpleNamespace(returncode=1)):
            file,mime,size=self.library.read(self.view,item['id'],True)
            with file:self.assertEqual(file.read(),payload)
        self.assertEqual(mime,'image/png')
        self.assertEqual(size,len(payload))

    def test_identical_copies_in_two_roots_are_shown_once(self):
        other=Path(self.tmp.name)/'work2';other.mkdir()
        (self.root/'docs').mkdir();(other/'docs').mkdir()
        payload=b'png-bytes-same-copy'
        first=self.root/'docs/screen.png';second=other/'docs/screen.png'
        first.write_bytes(payload);second.write_bytes(payload)
        now=int(time.time())
        os.utime(first,(now-30,now-30))
        os.utime(second,(now-5,now-5))
        (other/'docs/other.png').write_bytes(b'different-file')
        self.view['agents'].append({'identity':'claude:87654321-4321-8765-4321-cba987654321',
            'cwd':str(other),'name':'port','workspace_id':'w1'})
        items=self.library.discover(self.view)
        screens=[i for i in items if i['name']=='screen.png']
        self.assertEqual(len(screens),1)
        self.assertEqual(screens[0]['relative_path'],'docs/screen.png')
        self.assertEqual(screens[0]['size'],len(payload))
        self.assertEqual(screens[0]['path'],str(second))
        self.assertEqual({i['name'] for i in items},{'screen.png','other.png'})

    def test_same_name_at_a_different_path_or_size_is_kept(self):
        items=[
            {'id':'a','name':'ready-720.png','relative_path':'docs/setup/ready-720.png','size':10,'at':2,'source':'workspace'},
            {'id':'b','name':'ready-720.png','relative_path':'docs/onboarding/ready-720.png','size':11,'at':2,'source':'workspace'},
            {'id':'c','name':'icon.png','relative_path':'docs/icon.png','size':8,'at':3,'source':'trace'},
            {'id':'d','name':'icon.png','relative_path':'docs/icon.png','size':9,'at':1,'source':'workspace'},
            {'id':'e','name':'copy.png','relative_path':'docs/copy.png','size':4,'at':5,'source':'workspace'},
            {'id':'f','name':'copy.png','relative_path':'docs/copy.png','size':4,'at':6,'source':'trace'},
        ]
        collapsed={i['id'] for i in collapse_identical(items)}
        # a and b are different paths; c and d are the same path but different sizes; e is replaced by newer f
        self.assertEqual(collapsed,{'a','b','c','d','f'})


class MediaAuth(unittest.TestCase):
    def test_all_media_routes_require_login_and_recheck_watched_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'frame.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            c=make_collector();self.addCleanup(c.store.close)
            c.engine.agents['claude:a']['cwd']=str(root)
            server=PhoneServer(('127.0.0.1',0),make_handler(c,local=False));serve(server)
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            port=server.server_address[1]
            def request(path,method='GET',body=None,cookie=None,extra=None):
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                headers={'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json',**(extra or {})}
                if cookie:headers['Cookie']=cookie
                conn.request(method,path,json.dumps(body) if body else None,headers)
                res=conn.getresponse();data=res.read();status=res.status;head=dict(res.getheaders());conn.close()
                return status,head,data
            for url in ('/api/visuals','/api/visuals/'+'a'*32+'/file','/api/visuals/'+'a'*32+'/thumbnail'):
                self.assertEqual(request(url)[0],401)
            _,headers,_=request('/login','POST',{'passphrase':PASSPHRASE})
            cookie=headers['Set-Cookie'].split(';')[0]
            status,_,raw=request('/api/visuals?identity=claude:a',cookie=cookie)
            self.assertEqual(status,200);item,=json.loads(raw)['items']
            status,headers,raw=request(item['url'],cookie=cookie)
            self.assertEqual(status,200);self.assertIn(b'<svg',raw);self.assertIn('sandbox',headers['Content-Security-Policy'])
            self.assertEqual(request(item['url'],cookie=cookie,extra={'Range':'bytes=0-3'})[2],b'<svg')
            self.assertEqual(request(item['url'],cookie=cookie,extra={'Range':'bytes=9000-9999'})[0],416)
            c.settings.set_override('watch.enabled_workspaces',[])
            self.assertEqual(request(item['url'],cookie=cookie)[0],404)

    @unittest.skipUnless(BROWSER,'no Chromium')
    def test_gallery_lazy_load_viewer_and_reply_draft_survive_polls(self):
        f=page_fixture();identity=f['view']['agents'][0]['identity']
        f['visuals']={'desktop':True,'items':[{'id':'a'*32,'name':'screen.svg','path':'/work/screen.svg','relative_path':'screen.svg',
          'identity':identity,'agent':'builder','at':100,'video':False,'source':'trace','url':'data:image/svg+xml,<svg/>','thumbnail':'data:image/svg+xml,<svg/>'}]}
        result=reading(run(f,'/a/'+identity+'/activity',412,915,script="""
          const before=window.__calls.filter(c=>c[0].startsWith('/api/visuals')).length;
          document.querySelector('.latest-visuals').open=true;await new Promise(r=>setTimeout(r,300));
          document.querySelector('.visual-tile').click();
          const dialog=document.querySelector('#visual-viewer');
          FIXTURE.view.version++;await refresh();
          const kept=dialog===document.querySelector('#visual-viewer')&&dialog.open;
          [...dialog.querySelectorAll('button')].find(b=>b.textContent==='Reply about this').click();
          document.body.dataset.reading=JSON.stringify({before,kept,text:app.composer.text,target:app.composer.identity,
            sends:window.__calls.filter(c=>c[0]==='/api/replies'&&c[1]==='POST').length});
        """))
        self.assertEqual(result['before'],0);self.assertTrue(result['kept']);self.assertIn('/work/screen.svg',result['text'])
        self.assertEqual(result['target'],identity);self.assertEqual(result['sends'],0)

    @unittest.skipUnless(BROWSER,'no Chromium')
    def test_coordinator_page_renders_one_latest_visuals_strip(self):
        f=page_fixture();agent=f['view']['agents'][0];agent['is_coordinator']=True
        result=reading(run(f,'/a/'+agent['identity']+'/status',390,900,script="""
          document.body.dataset.reading=JSON.stringify({
            strips:document.querySelectorAll('.latest-visuals').length,
            keys:[...document.querySelectorAll('.latest-visuals')].map((s)=>s.getAttribute('data-reading-key'))
          });
        """))
        self.assertEqual(result['strips'],1)
        self.assertEqual(result['keys'],[f"visuals:team:{agent['identity']}"])
        worker=page_fixture();wid=worker['view']['agents'][0]['identity']
        plain=reading(run(worker,'/a/'+wid+'/activity',390,900,script="""
          document.body.dataset.reading=JSON.stringify({
            strips:document.querySelectorAll('.latest-visuals').length,
            keys:[...document.querySelectorAll('.latest-visuals')].map((s)=>s.getAttribute('data-reading-key'))
          });
        """))
        self.assertEqual(plain['strips'],1)
        self.assertEqual(plain['keys'],[f'visuals:agent:{wid}'])
