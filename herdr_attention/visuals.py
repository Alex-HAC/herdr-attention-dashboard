"""Bounded, on-demand media discovery and capability URLs inside watched roots.

A URL never accepts a path. Every read rechecks current watched roots and opens
all path components without following symlinks, including after discovery.
"""
from __future__ import annotations
import fnmatch
import io
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from .traces import split_identity
from .ui_contract import api_path

EXTENSIONS = ['png','jpg','jpeg','webp','gif','svg','mp4','webm','mov']
IGNORE = ['.git','node_modules','build','dist','.cache','__pycache__','.venv','venv']
VIDEO = {'mp4','webm','mov'}
MAX_BYTES = 256 * 1024 * 1024
# Always skipped by the disk walk, whatever visuals.ignore_globs says: these trees are huge
# and never hold work an agent wants to show. Files named in a trace are still found.
HEAVY = frozenset({'.git','.hg','.svn','node_modules','.venv','venv','env','__pycache__','.cache','.tox',
    '.mypy_cache','.pytest_cache','.ruff_cache','site-packages','build','dist','target','out','.next',
    '.nuxt','.gradle','.idea','Library','Temp','obj','vendor','.terraform'})
SCAN_BUDGET = 4.0       # seconds of wall time for one discovery
MAX_WALK_FILES = 4000
MAX_WALK_DIRS = 300
MAX_TRACE_HITS = 400    # extension hits inspected per trace string
MIN_RESCAN = 3.0        # a forced refresh never rescans more often than this
_STOP = frozenset(' \t\r\n\x0b\x0c"\x27`<>')
_NAME = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_. -')
_DIR = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_~./-')
_QUOTED = re.compile(r'["\x27`]([^"\x27`]{1,400})["\x27`]')


class Budget:
    """Wall-clock allowance for one scan. `hit` records that work was cut short."""
    def __init__(self, seconds=SCAN_BUDGET):
        self.deadline=time.monotonic()+seconds;self.hit=False;self.reason=''
    def remaining(self):
        return max(0.0,self.deadline-time.monotonic())
    def expired(self):
        if time.monotonic()>=self.deadline:self.stop('time budget reached');return True
        return False
    def stop(self,reason):
        if not self.hit:self.hit=True;self.reason=reason


def path_candidates(value, extension_pattern, limit=MAX_TRACE_HITS):
    """Media paths named in one string, in time linear in its length.

    Finds each ".ext" first and only then looks a bounded distance backwards, so a megabyte
    of base64 or minified text cannot make the scan backtrack.
    """
    found=[];hits=0
    for match in extension_pattern.finditer(value):
        hits+=1
        if hits>limit:break
        dot=match.start();end=match.end();low=max(0,dot-255)
        start=dot
        while start>low and value[start-1] not in _STOP:start-=1
        if start<dot:found.append(value[start:end])
        # A file name may hold spaces; the folders in front of it may not.
        spaced=dot
        while spaced>low and value[spaced-1] in _NAME:spaced-=1
        if spaced>low and value[spaced-1]=='/':
            while spaced>low and value[spaced-1] in _DIR:spaced-=1
        name=value[spaced:end].strip()
        if len(name)>len(match.group()) and name!=value[start:end]:found.append(name)
    return found


def ignored(relative, patterns):
    return any(fnmatch.fnmatch(relative.as_posix(), p) or any(fnmatch.fnmatch(part,p) for part in relative.parts) for p in patterns)


def open_beneath(root: Path, relative: Path):
    if relative.is_absolute() or not relative.parts or any(p in ('..','.') for p in relative.parts):
        raise ValueError('invalid media path')
    if not root.is_absolute():raise ValueError('invalid media root')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in (*root.parts[1:],*relative.parts[:-1]):
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=next_fd
        final=os.open(relative.parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    finally:os.close(fd)
    info=os.fstat(final)
    if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_BYTES:
        os.close(final);raise ValueError('media must be a bounded regular file')
    return os.fdopen(final,'rb'),info


THUMB_PX = 320
THUMB_CACHE_MAX = 2000
THUMB_URL_GEN = 2  # bump when thumbnail bytes or cache rules change, so browsers refetch


def is_valid_thumbnail(path: Path) -> bool:
    """True when the file looks like a real JPEG or WebP, not a truncated or test stub."""
    try:
        if path.stat().st_size < 32:
            return False
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    if head.startswith(bytes.fromhex("ffd8ff")):
        return True
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return True
    return False


def placeholder_jpeg_bytes() -> bytes:
    """A tiny valid JPEG used only when no preview and no original image can be served."""
    return bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000"
        "ffdb004300010101010101010101010101010101010101010101010101010101010101010101"
        "0101010101010101010101010101010101010101010101010101"
        "ffc0000b080001000101011100"
        "ffc40014100100000000000000000000000000000000"
        "ffc40014100000000000000000000000000000000000"
        "ffda000800010100003f00fbffd9"
    )


def cached_thumbnail(cache: Path, source: str, file, extension: str,
                     media_id: str | None = None, mtime_ns: int = 0) -> Path | None:
    """A 320 px WebP or JPEG of the first frame, or None when ffmpeg cannot make one.

    Decodes only from the open, already checked descriptor ``file``: no shell, no network
    protocols. Cached in ``cache`` under ``{media_id}-{mtime_ns}`` (or a hash of ``source``).
    """
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    if media_id and len(media_id) == 32 and all(c in "0123456789abcdef" for c in media_id):
        stem = f"{media_id}-{int(mtime_ns)}"
    else:
        stem = hashlib.sha256(source.encode()).hexdigest()
    webp = cache / f"{stem}.webp"
    jpeg = cache / f"{stem}.jpg"
    for existing in (webp, jpeg):
        if existing.exists() and is_valid_thumbnail(existing):
            return existing
        if existing.exists():
            existing.unlink(missing_ok=True)
    old = sorted(list(cache.glob("*.jpg")) + list(cache.glob("*.webp")),
                 key=lambda p: p.stat().st_mtime, reverse=True)
    for entry in old[THUMB_CACHE_MAX - 1:]:
        entry.unlink(missing_ok=True)
    formats = {'png': ['-f', 'image2pipe', '-c:v', 'png'], 'jpg': ['-f', 'image2pipe', '-c:v', 'mjpeg'],
               'jpeg': ['-f', 'image2pipe', '-c:v', 'mjpeg'], 'webp': ['-f', 'image2pipe', '-c:v', 'webp'],
               'gif': ['-f', 'gif'], 'mp4': ['-f', 'mov'], 'mov': ['-f', 'mov'], 'webm': ['-f', 'matroska']}
    if extension not in formats:
        return None
    scale = f"scale={THUMB_PX}:{THUMB_PX}:force_original_aspect_ratio=decrease"
    attempts = (
        (webp, [ "-f", "webp", "-c:v", "libwebp", "-quality", "75"]),
        (jpeg, [ "-f", "image2", "-c:v", "mjpeg", "-q:v", "6"]),
    )
    for cached, encode in attempts:
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(suffix=cached.suffix, dir=cache)
            os.close(fd)
            process = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                 *formats[extension], "-i", f"/proc/self/fd/{file.fileno()}",
                 "-frames:v", "1", "-vf", scale, "-threads", "1", *encode, "-y", temporary],
                pass_fds=(file.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
            if process.returncode == 0 and is_valid_thumbnail(Path(temporary)):
                os.replace(temporary, cached)
                temporary = None
                if cached.exists() and is_valid_thumbnail(cached):
                    return cached
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        try:
            file.seek(0)
        except OSError:
            pass
    return None


def collapse_identical(items):
    """One entry per repo-relative path and size.

    Agents often work in copies of the same tree (a UI checkout and an app-base
    checkout, an install tree and the original). The same screenshot then has two
    media ids because each full path is different. Those copies should appear once.
    Different paths or different sizes stay separate, even when the file names match.
    The newest reference is kept; a trace mention wins a tie.
    """
    chosen={}
    for item in items:
        key=(item['relative_path'], item['size'])
        previous=chosen.get(key)
        if previous is None:
            chosen[key]=item
            continue
        newer=item['at']>previous['at']
        same=item['at']==previous['at'] and item['source']=='trace' and previous['source']!='trace'
        if newer or same:
            chosen[key]=item
    return list(chosen.values())


class Visuals:
    def __init__(self, settings, locator, cache, clock=time.time):
        self.settings=settings;self.locator=locator;self.cache=Path(cache);self.clock=clock
        self.records={};self.snapshots={};self.root_cache={};self.lock=threading.RLock()
        # One scan at a time. Never taken by read(), /api/view or /api/health.
        self.scan_lock=threading.Lock()
        # Global hub catalog, filled by rotating background scans (v0.9.9).
        self.catalog={};self.batch_index=0;self.store=None;self.last_partial=''

    def extensions(self):
        return {str(e).lower().lstrip('.') for e in self.settings.get('visuals.extensions',EXTENSIONS) if re.fullmatch(r'\.?[a-zA-Z0-9]{2,8}',str(e))}

    def roots(self,view,budget=None):
        roots={}
        for a in view['agents']:
            if not a.get('cwd'):continue
            try:
                cwd=Path(a['cwd']).expanduser().resolve(strict=True)
                if not cwd.is_dir() or cwd==Path('/'):continue
                with self.lock:cached=self.root_cache.get(cwd)
                if cached and self.clock()-cached[0]<30:root=cached[1]
                elif budget is not None and budget.expired():
                    # Out of time: reuse the last known root rather than start another git.
                    root=cached[1] if cached else cwd
                else:
                    # git runs outside the lock so thumbnails and other scans never wait on it.
                    root=cwd
                    try:
                        limit=2 if budget is None else max(0.2,min(2,budget.remaining()))
                        result=subprocess.run(['git','-C',str(cwd),'rev-parse','--show-toplevel'],capture_output=True,text=True,timeout=limit)
                        if result.returncode==0:
                            top=Path(result.stdout.strip()).resolve(strict=True)
                            if top!=Path('/') and top.is_dir() and cwd.is_relative_to(top):root=top
                    except (OSError,ValueError,subprocess.TimeoutExpired):pass
                    with self.lock:
                        self.root_cache[cwd]=(self.clock(),root)
                        while len(self.root_cache)>128:self.root_cache.pop(next(iter(self.root_cache)))
                roots[a['identity']]=root
            except (OSError,RuntimeError):pass
        return roots

    def _trace_paths(self,identity,cutoff,budget=None,extensions=None):
        parts=split_identity(identity)
        path=self.locator.find(*parts) if parts else None
        if not path:return []
        try:
            info=path.stat();trace_at=info.st_mtime
            if trace_at<cutoff:return []
            with path.open('rb') as fh:
                fh.seek(max(0,info.st_size-1024*1024));raw=fh.read(1024*1024).decode('utf8','replace')
        except OSError:return []
        extensions=extensions if extensions is not None else self.extensions()
        if not extensions:return []
        extension_pattern=re.compile(r'\.(?:'+'|'.join(re.escape(e) for e in sorted(extensions))+r')\b',re.I)
        found=[]
        def strings(value,depth=0):
            if isinstance(value,dict):
                for v in value.values():yield from strings(v,depth)
            elif isinstance(value,list):
                for v in value:yield from strings(v,depth)
            elif isinstance(value,str):
                yield value
                if depth<2 and value.startswith(('{','[')):
                    try:yield from strings(json.loads(value),depth+1)
                    except ValueError:pass
        for line in raw.splitlines()[-1000:]:
            if budget is not None and budget.expired():break
            # Cheap C-level test first: most trace lines name no media at all.
            if not extension_pattern.search(line):continue
            try:record=json.loads(line)
            except ValueError:continue
            at=trace_at
            timestamp=record.get('timestamp') or record.get('at') if isinstance(record,dict) else None
            if timestamp:
                try:
                    at=float(timestamp) if isinstance(timestamp,(int,float)) else datetime.fromisoformat(timestamp.replace('Z','+00:00')).timestamp()
                    if at<cutoff:continue
                except (ValueError,TypeError):pass
            for value in strings(record):
                if not extension_pattern.search(value):continue
                # Quoted strings preserve spaces; plain prose uses path tokens.
                for quoted in _QUOTED.findall(value):
                    if Path(quoted).suffix.lower().lstrip('.') in extensions:found.append((quoted,at))
                found.extend((name,at) for name in path_candidates(value,extension_pattern))
            if len(found)>20000:found=found[-500:]
        return found[-500:]

    def _disk_paths(self,root,cutoff,patterns,budget=None,extensions=None):
        budget=budget or Budget();extensions=extensions if extensions is not None else self.extensions()
        candidates=[]
        try:
            result=subprocess.run(['git','-C',str(root),'status','--porcelain=v1','-z','--untracked-files=all'],capture_output=True,timeout=max(0.2,min(3,budget.remaining())))
            records=result.stdout[:256*1024].split(b'\0');skip=False
            for record in records:
                if skip:skip=False;continue
                if len(record)<4:continue
                skip=record[:1] in (b'R',b'C') or record[1:2] in (b'R',b'C')
                candidates.append(os.fsdecode(record[3:]))
        except (OSError,subprocess.TimeoutExpired):pass
        # Own walk instead of os.walk: every directory entry counts against the caps, so one
        # folder with a million files cannot be listed in full before a limit applies.
        count=0;dirs_seen=0;stack=[(str(root),Path('.'))]
        while stack:
            if budget.expired():break
            if dirs_seen>=MAX_WALK_DIRS:budget.stop('folder limit reached');break
            folder,relative=stack.pop();dirs_seen+=1;below=[]
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        count+=1
                        if count>MAX_WALK_FILES:budget.stop('file limit reached');break
                        if count%256==0 and budget.expired():break
                        name=entry.name
                        try:
                            if entry.is_symlink():continue
                            if entry.is_dir(follow_symlinks=False):
                                if name not in HEAVY and len(relative.parts)<8 and not ignored(relative/name,patterns):below.append(name)
                                continue
                            if name.rpartition('.')[2].lower() not in extensions:continue
                            if entry.stat(follow_symlinks=False).st_mtime>=cutoff:candidates.append(str(relative/name))
                        except OSError:pass
            except OSError:continue
            if budget.hit:break
            stack.extend((os.path.join(folder,name),relative/name) for name in sorted(below,reverse=True))
        return candidates

    def discover(self,view,identity=None,workspace=None,refresh=False):
        return self.scan(view,identity,workspace,refresh)['items']

    def budget_seconds(self):
        try:return min(15.0,max(0.5,float(self.settings.get('visuals.scan_budget_seconds',SCAN_BUDGET))))
        except (TypeError,ValueError):return SCAN_BUDGET

    def scan(self,view,identity=None,workspace=None,refresh=False):
        """Bounded discovery: {'items', 'partial', 'partial_reason'}.

        Holds only scan_lock (and self.lock for dictionary updates), stops at a wall-clock
        budget and at file and folder caps, and is cached for 30 seconds.
        """
        budget=Budget(self.budget_seconds())
        roots=self.roots(view,budget)
        agents=[a for a in view['agents'] if a['identity'] in roots and (not identity or a['identity']==identity) and (not workspace or a['workspace_id']==workspace)]
        if identity and not any(a['identity']==identity for a in agents):raise ValueError('no such watched agent')
        if workspace and not any(w['id']==workspace for w in view['workspaces']):raise ValueError('no such watched workspace')
        key=tuple((a['identity'],str(roots[a['identity']])) for a in agents)
        def fresh():
            with self.lock:cached=self.snapshots.get(key)
            if cached and self.clock()-cached[0]<(MIN_RESCAN if refresh else 30):return cached
            return None
        def answer(cached):
            return {'items':cached[1],'partial':bool(cached[2]),'partial_reason':cached[2]}
        cached=fresh()
        if cached:return answer(cached)
        if not self.scan_lock.acquire(timeout=budget.remaining()):
            # Another scan is still running. Answer now with what is known, never queue up.
            with self.lock:stale=self.snapshots.get(key)
            return {'items':stale[1] if stale else [],'partial':True,'partial_reason':'another scan is still running'}
        try:
            cached=fresh()
            if cached:return answer(cached)
            return self._scan(view,agents,roots,key,budget)
        finally:self.scan_lock.release()

    def _scan(self,view,agents,roots,key,budget):
        cutoff=self.clock()-max(1,float(self.settings.get('visuals.recent_hours',48)))*3600
        patterns=self.settings.get('visuals.ignore_globs',IGNORE);extensions=self.extensions()
        disk={};items={}
        if len(agents)>30:budget.stop('agent limit reached')
        for a in agents[:30]:
            if budget.expired():break
            root=roots[a['identity']]
            if root not in disk:disk[root]=self._disk_paths(root,cutoff,patterns,budget,extensions)
            references=self._trace_paths(a['identity'],cutoff,budget,extensions)
            cwd_relative=Path(a['cwd']).expanduser().resolve().relative_to(root)
            if cwd_relative.parts:
                references += [(str(cwd_relative/Path(raw)),at) for raw,at in list(references) if not Path(raw).is_absolute()]
            checked=0
            for source,candidates in (('workspace',disk[root]),('trace',references)):
                for candidate in candidates:
                    checked+=1
                    if checked%64==0 and budget.expired():break
                    raw,referenced_at=candidate if source=='trace' else (candidate,0)
                    path=Path(raw).expanduser()
                    if '..' in path.parts:continue
                    if path.is_absolute():
                        try:relative=path.relative_to(root)
                        except ValueError:continue
                    else:relative=path
                    if relative.suffix.lower().lstrip('.') not in extensions or ignored(relative,patterns):continue
                    try:
                        file,info=open_beneath(root,relative);file.close()
                        if source=='workspace' and info.st_mtime<cutoff:continue
                    except (OSError,ValueError):continue
                    full=root/relative;media_id=hashlib.sha256(os.fsencode(full)).hexdigest()[:32]
                    previous=items.get(media_id)
                    if previous and (source!='trace' or (previous['source']=='trace' and previous['at']>=max(info.st_mtime,referenced_at))):continue
                    items[media_id]={'id':media_id,'name':relative.name,'path':str(full),'relative_path':str(relative),
                        'identity':a['identity'],'agent':a.get('name') or a['identity'],'source':source,
                        'at':max(info.st_mtime,referenced_at),'modified_at':info.st_mtime,'size':info.st_size,'video':relative.suffix.lower().lstrip('.') in VIDEO,
                        'url':api_path('visual_file',media_id),'thumbnail':api_path('visual_thumbnail',media_id)}
                    with self.lock:self.records[media_id]=(root,relative)
        result=sorted(collapse_identical(items.values()),key=lambda i:i['at'],reverse=True)[:60]
        reason=budget.reason if budget.hit else ''
        with self.lock:
            self.snapshots[key]=(self.clock(),result,reason)
            if len(self.snapshots)>40:self.snapshots.pop(next(iter(self.snapshots)))
            while len(self.records)>2048:self.records.pop(next(iter(self.records)))
        return {'items':result,'partial':bool(reason),'partial_reason':reason}

    def _root_allowed(self, view, root) -> bool:
        """True if ``root`` is a watched workspace directory (or its git top). No git calls."""
        enabled = set(self.settings.get('watch.enabled_workspaces') or [])
        if not enabled:
            return False
        root = Path(root)
        for agent in view.get('agents') or []:
            workspace = agent.get('workspace_id') or agent.get('workspace')
            if workspace and workspace not in enabled:
                continue
            cwd = agent.get('cwd')
            if not cwd:
                continue
            try:
                path = Path(cwd).expanduser().resolve(strict=True)
            except OSError:
                continue
            if path == root or path.is_relative_to(root):
                return True
            with self.lock:
                cached = self.root_cache.get(path)
            if cached and cached[1] == root:
                return True
        return False

    def thumbnail_etag(self, media_id: str) -> str | None:
        """Stable ETag for a cached preview: media id plus the source file's mtime."""
        with self.lock:
            record = self.records.get(media_id)
            item = self.catalog.get(media_id)
        if not record:
            return None
        mtime = 0
        if item and item.get('modified_at') is not None:
            try:
                mtime = int(float(item['modified_at']))
            except (TypeError, ValueError):
                mtime = 0
        size = 0
        if self.cache.is_dir():
            for cached in (*self.cache.glob(f"{media_id}-*.webp"), *self.cache.glob(f"{media_id}-*.jpg")):
                if is_valid_thumbnail(cached):
                    size = cached.stat().st_size
                    break
        token = f"{media_id}-{mtime}-{size}-{THUMB_URL_GEN}"
        return '"' + hashlib.sha256(token.encode()).hexdigest()[:16] + '"'

    def prepare_thumbnails(self, view, items, limit=12) -> int:
        """Generate a few missing previews in the background. Never serves originals."""
        made = 0
        for item in items:
            if made >= limit or not item.get('id'):
                break
            try:
                file, mime, size = self.read(view, item['id'], thumbnail=True)
                file.close()
                made += 1
            except (ValueError, OSError):
                continue
        return made

    def read(self,view,media_id,thumbnail=False):
        with self.lock:record=self.records.get(media_id)
        if not record:raise ValueError('no such visual')
        root,relative=record
        # Authorize against watched cwds already in the view. Do not run git on every tile.
        if (not self._root_allowed(view, root) or ignored(relative,self.settings.get('visuals.ignore_globs',IGNORE))
                or relative.suffix.lower().lstrip('.') not in self.extensions()):
            raise ValueError('visual is outside watched roots')
        file,info=open_beneath(root,relative)
        mime=mimetypes.guess_type(relative.name)[0] or 'application/octet-stream'
        extension=relative.suffix.lower().lstrip('.')
        if thumbnail and extension=='svg':
            # Keep SVG in the browser's isolated image context; never resolve its references in a decoder.
            if info.st_size>4*1024*1024:file.close();raise ValueError('SVG preview too large')
            return file,mime,info.st_size
        if thumbnail:
            cached=cached_thumbnail(self.cache,f'{root/relative}:{info.st_mtime_ns}:{info.st_size}',file,extension,
                                    media_id=media_id, mtime_ns=info.st_mtime_ns)
            if cached:
                file.close()
                kind='image/webp' if cached.suffix.lower()=='.webp' else 'image/jpeg'
                return cached.open('rb'),kind,cached.stat().st_size
            # Prefer the original image over a 1x1 placeholder so a failed preview still shows something.
            if extension in {'png','jpg','jpeg','webp','gif'}:
                try:
                    file.seek(0)
                    return file,mime,info.st_size
                except OSError:
                    file.close()
                    file,info=open_beneath(root,relative)
                    return file,mime,info.st_size
            file.close()
            data=placeholder_jpeg_bytes()
            return io.BytesIO(data),'image/jpeg',len(data)
        return file,mime,info.st_size


    def attach_store(self, store):
        self.store = store

    def _seen_state(self):
        if not self.store:
            return set(), 0.0
        return self.store.visuals_seen_state()

    def item_is_seen(self, item, seen_ids=None, seen_all_at=None):
        if seen_ids is None:
            seen_ids, seen_all_at = self._seen_state()
        return item['id'] in seen_ids or item['at'] <= (seen_all_at or 0)

    def merge_catalog(self, items):
        """Fold newly found items into the hub catalog, collapsing identical copies."""
        with self.lock:
            for item in items:
                self.catalog[item['id']] = item
            cutoff = self.clock() - max(1, float(self.settings.get('visuals.recent_hours', 48))) * 3600
            kept = [i for i in collapse_identical(self.catalog.values()) if i['at'] >= cutoff]
            self.catalog = {i['id']: i for i in kept}

    def decorate(self, item, view, seen_ids=None, seen_all_at=None):
        """Add seen, download URL, workspace and pane for the hub, toasts and the viewer."""
        if seen_ids is None:
            seen_ids, seen_all_at = self._seen_state()
        agents = {a['identity']: a for a in view.get('agents') or []}
        agent = agents.get(item.get('identity'))
        out = dict(item)
        out['seen'] = self.item_is_seen(item, seen_ids, seen_all_at)
        out['download'] = api_path('visual_download', item['id'])
        stamp = item.get('modified_at') or item.get('at') or 0
        try:
            stamp = int(float(stamp))
        except (TypeError, ValueError):
            stamp = 0
        out['thumbnail'] = api_path('visual_thumbnail', item['id']) + f'?v={stamp}-{THUMB_URL_GEN}'
        if agent:
            out['workspace_id'] = agent.get('workspace_id') or out.get('workspace_id') or ''
            out['workspace_label'] = agent.get('workspace_label') or out.get('workspace_label') or ''
            out['pane_id'] = agent.get('pane_id') or out.get('pane_id') or ''
            if agent.get('name'):
                out['agent'] = agent['name']
        else:
            out.setdefault('workspace_id', '')
            out.setdefault('workspace_label', '')
            out.setdefault('pane_id', '')
        return out

    def decorate_result(self, view, result):
        """Keep strip payloads working and add hub fields to each item."""
        seen_ids, seen_all_at = self._seen_state()
        items = [self.decorate(i, view, seen_ids, seen_all_at) for i in result.get('items') or []]
        unseen = sum(1 for i in items if not i['seen'])
        return {**result, 'items': items, 'unseen': unseen}

    def ingest(self, view, batch=30):
        """Scan the next batch of agents and merge what is found into the catalog."""
        budget = Budget(self.budget_seconds())
        roots = self.roots(view, budget)
        agents = [a for a in view['agents'] if a['identity'] in roots]
        if not agents:
            return {'items': [], 'partial': False, 'partial_reason': ''}
        start = self.batch_index % len(agents)
        chosen = agents[start:start + batch]
        if len(chosen) < batch and start:
            chosen += agents[:batch - len(chosen)]
        self.batch_index = (start + len(chosen)) % len(agents)
        key = tuple((a['identity'], str(roots[a['identity']])) for a in chosen)
        result = self._scan(view, chosen, roots, key, budget)
        self.merge_catalog(result['items'])
        self.last_partial = result.get('partial_reason') or ''
        return result

    def _filtered(self, view, identity=None, workspace=None):
        agents = {a['identity']: a for a in view.get('agents') or []}
        items = list(self.catalog.values())
        if identity:
            items = [i for i in items if i.get('identity') == identity]
        if workspace:
            items = [i for i in items if agents.get(i.get('identity'), {}).get('workspace_id') == workspace]
        return sorted(items, key=lambda i: i['at'], reverse=True)

    def feed(self, view, *, identity=None, workspace=None, before=None, limit=24):
        """Paged hub listing from the catalog (already collapsed)."""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 24
        limit = max(1, min(60, limit))
        try:
            before = float(before) if before is not None and before != '' else None
        except (TypeError, ValueError):
            raise ValueError('bad cursor')
        seen_ids, seen_all_at = self._seen_state()
        items = self._filtered(view, identity, workspace)
        unseen = sum(1 for i in items if not self.item_is_seen(i, seen_ids, seen_all_at))
        if before is not None:
            items = [i for i in items if i['at'] < before]
        page = items[:limit]
        decorated = [self.decorate(i, view, seen_ids, seen_all_at) for i in page]
        return {
            'items': decorated,
            'unseen': unseen,
            'next': decorated[-1]['at'] if len(page) == limit else None,
            'partial': bool(self.last_partial),
            'partial_reason': self.last_partial,
        }

    def snapshot(self, view, limit=5):
        """Cheap badge and toast payload for /api/view. Never starts a scan."""
        seen_ids, seen_all_at = self._seen_state()
        items = self._filtered(view)
        unseen_items = [i for i in items if not self.item_is_seen(i, seen_ids, seen_all_at)]
        return {
            'unseen': len(unseen_items),
            'items': [self.decorate(i, view, seen_ids, seen_all_at) for i in unseen_items[:limit]],
            'latest_at': items[0]['at'] if items else None,
        }

    def filename(self, media_id):
        """Safe download name for a discovered visual; None if it is not known."""
        with self.lock:
            record = self.records.get(media_id)
            item = self.catalog.get(media_id)
        if item:
            return item.get('name')
        if record:
            return record[1].name
        return None
