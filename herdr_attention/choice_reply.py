"""Map an explicit answer to a freshly read numbered terminal choice menu."""
import re
from .sanitize import readable


def numbered_rows(screen):
    rows = []
    for line in readable(screen).splitlines():
        match = re.match(r'^\s*[│┃ ]*([❯>›●]?)\s*(\d+)[.)]\s+([^│┃]+)', line)
        if match:
            rows.append((bool(match[1]), f'{match[2]}. {match[3].strip()}'))
    return rows


def menu_keys(question, option, screen, provider='claude'):
    normalize = lambda value: re.sub(r'\W+', ' ', value.casefold()).strip()
    if not question or not question.get('terminal') or normalize(question['text']) not in normalize(readable(screen)):
        raise ValueError('The choice dialog changed. Refresh before answering.')
    rows = numbered_rows(screen)
    selected = [i for i, row in enumerate(rows) if row[0]]
    options = [normalize(row[1]) for row in rows]
    wanted = normalize(option)
    known = [normalize(value) for value in question.get('options', [])]
    if len(rows) < 2 or len(rows) > 8 or wanted not in options or wanted not in known or len(set(options)) != len(options):
        raise ValueError('This menu has no verified key mapping. Answer in the terminal.')
    if provider == 'codex':
        # Codex selects AND submits the digit. An extra Enter could answer a new dialog.
        return [re.match(r'\d+', rows[options.index(wanted)][1])[0]]
    if len(selected) != 1:
        raise ValueError('This menu has no verified key mapping. Answer in the terminal.')
    offset = options.index(wanted) - selected[0]
    return ['down' if offset > 0 else 'up'] * abs(offset) + ['enter']
