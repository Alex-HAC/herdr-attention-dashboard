"""Installer support for the optional overview wrapper and Lua hotkey.

Kept separate so it can be checked in a temporary HOME without deploying.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import time

from .ui_contract import CONTRACT, desktop_url

START = '-- BEGIN herdr-attention overview'
END = '-- END herdr-attention overview'


def install(app: Path, bin_dir: Path, bindings: Path, backup: Path) -> None:
    from .config import Settings, load_file
    # Validate the same config used by the collector and launch wrapper.
    desktop_url(Settings(load_file(None)))
    old = bindings.read_text() if bindings.exists() else ''
    clean = old
    if START in old:
        start, rest = old.split(START, 1)
        _, end = rest.split(END, 1)
        clean = start + end.lstrip('\n')
    hotkey = CONTRACT['desktop']['hotkey']
    # Refuse to silently displace an existing user binding.
    for line in clean.splitlines():
        if not line.lstrip().startswith('--') and hotkey in line:
            raise RuntimeError(f'{hotkey} is already bound; keeping existing binding')
    try:
        live = json.loads(subprocess.run(['hyprctl', 'binds', '-j'], check=True,
                                        capture_output=True, text=True, timeout=5).stdout)
        conflict = any(b.get('key', '').upper() == 'H' and b.get('modmask') == 65
                       and 'herdr-attention-overview' not in b.get('arg', '') for b in live)
        if conflict:
            raise RuntimeError(f'{hotkey} is already bound; keeping existing binding')
    except (OSError, ValueError, subprocess.SubprocessError):
        pass  # Offline installation: the source check still applies.
    wrapper = bin_dir / 'herdr-attention-overview'
    source = ('#!/usr/bin/python3 -s\nimport sys\n'
              f'sys.path.insert(0, {str(app)!r})\n'
              'from herdr_attention.desktop import overview_main\n'
              'sys.exit(overview_main())\n')
    backup.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()
    for path in (wrapper, bindings):
        if path.exists():
            shutil.copy2(path, backup / f'{path.name}.{stamp}')
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(source)
    wrapper.chmod(0o755)
    bindings.parent.mkdir(parents=True, exist_ok=True)
    block = (f'{START}\n'
             f'o.bind({json.dumps(hotkey)}, {json.dumps(CONTRACT["desktop"]["title"])}, '
             f'{json.dumps(str(wrapper))})\n{END}\n')
    bindings.write_text(clean.rstrip() + '\n\n' + block)


def remove(bindings: Path, backup: Path) -> None:
    """Remove only our marked block, preserving every other binding."""
    if not bindings.exists():
        return
    old = bindings.read_text()
    if START not in old:
        return
    start, rest = old.split(START, 1)
    _, end = rest.split(END, 1)
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bindings, backup / f'{bindings.name}.{time.time_ns()}')
    bindings.write_text(start.rstrip() + '\n' + end.lstrip('\n'))
