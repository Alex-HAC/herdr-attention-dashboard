#!/usr/bin/env python3
"""Find duplicate property/handler assignments inside one QML object block.

QML refuses to load a file where one object sets the same property twice
("Property value set multiple times"), and two handlers for the same signal
silently shadow each other in some cases. This scan catches both before the
file ever reaches the shell.

Usage: qml_dupes.py FILE.qml [...]    (exit 1 if anything is found)
"""

from __future__ import annotations

import re
import sys

OBJECT_OPEN = re.compile(r"^\s*(?:[A-Za-z_][\w.]*\s*:\s*)?([A-Z][\w.]*)\s*\{")
GROUP_OPEN = re.compile(r"^\s*([a-z][\w]*)\s*\{\s*$")
ASSIGN = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*:(?!:)")
PROPERTY = re.compile(r"^\s*(?:default\s+|required\s+|readonly\s+)*property\s+[\w<>.]+\s+(\w+)")
SIGNAL = re.compile(r"^\s*signal\s+(\w+)")
FUNCTION = re.compile(r"^\s*function\s+(\w+)\s*\(")
STRINGS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')
JS_KEYWORDS = {"case", "default", "return", "else", "var", "let", "const", "if", "for", "while"}


def strip(line: str) -> str:
    line = STRINGS.sub('""', line)
    return line.split("//", 1)[0]


def scan(path: str) -> list[str]:
    problems: list[str] = []
    # stack entries: (kind, name, start_line, seen: dict[key, line])
    stack: list[tuple[str, str, int, dict]] = [("root", "<file>", 0, {})]
    in_block_comment = False
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for number, raw in enumerate(lines, 1):
        line = raw
        if in_block_comment:
            if "*/" not in line:
                continue
            line = line.split("*/", 1)[1]
            in_block_comment = False
        if "/*" in line and "*/" not in line.split("/*", 1)[1]:
            line = line.split("/*", 1)[0]
            in_block_comment = True
        text = strip(line)
        top = stack[-1]

        if top[0] in ("object", "group", "root"):
            key = None
            for pattern, prefix in ((PROPERTY, "property "), (SIGNAL, "signal "), (FUNCTION, "function ")):
                m = pattern.match(text)
                if m:
                    key = prefix + m.group(1)
                    # `property int x: 1` also counts as an assignment of x
                    if prefix == "property " and re.match(r"\s*:", text.split(m.group(1), 1)[1]):
                        _record(problems, path, top, "assign " + m.group(1), number)
                    break
            if key is None:
                m = ASSIGN.match(text)
                if m and m.group(1) not in JS_KEYWORDS:
                    key = "assign " + m.group(1)
            if key:
                _record(problems, path, top, key, number)

        opens = text.count("{")
        closes = text.count("}")
        if opens > closes:
            if FUNCTION.match(text):
                kind = "js"
            elif top[0] != "js" and OBJECT_OPEN.match(text):
                kind = "object"
            elif top[0] in ("object", "root") and GROUP_OPEN.match(text):
                kind = "group"
            else:
                kind = "js"
            stack.append((kind, text.strip()[:40], number, {}))
            for _ in range(opens - closes - 1):
                stack.append(("js", "", number, {}))
        elif closes > opens:
            for _ in range(closes - opens):
                if len(stack) > 1:
                    stack.pop()
    return problems


def _record(problems: list[str], path: str, block: tuple, key: str, number: int) -> None:
    seen = block[3]
    if key in seen:
        problems.append(f"{path}:{number}: {key!r} already set at line {seen[key]} in {block[1]} (line {block[2]})")
    else:
        seen[key] = number


def main(paths: list[str]) -> int:
    found = []
    for path in paths:
        found += scan(path)
    for problem in found:
        print(problem)
    if not found:
        print(f"no duplicate assignments in {len(paths)} file(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
