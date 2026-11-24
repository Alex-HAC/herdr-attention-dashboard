"""Questions and decisions for Alex in an agent's last message (v0.9.6 items).

Contract: docs/briefs/V0.9.6-ITEMS-CONTRACT.md. ``agent.asks`` lists each separate
thing Alex can answer, so he can answer them one by one.

* Rules first, and the rules alone must work: a labelled "Decision/Question(s) for
  Alex" (inline or as a heading over a list), numbered questions, sentences ending
  in a question mark that are addressed to Alex, an explicit offer ("say if you want
  it"), and the direct ask the tier rules quote (attention.question_text).
* Never invent. Every item carries ``quote``, an exact span of ``original``. Choices
  only when the text offers them: Yes/No for a yes/no question or an offer, A/B (or
  "X, or Y") options copied from the text, a terminal dialog's own numbered options.
* The AI summary that already runs (summarize.py, its own account, no extra calls) may
  reword an item or add one, but only when its quote is found in ``original``.
* No item: ``state`` is "maybe" when the text may still want something (a question
  not addressed to Alex, "let me know", an AI ask without a quote), else "none".

The tier cap that goes with it is notify_tiers.asks_cap (one rule, in final_tier).
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache

from .ui_contract import CONTRACT

MAX_ORIGINAL = 24000          # the full message; the view's last_assistant.text stays 2000
MAX_ITEMS = 8
RULE_LIMIT = 3 * MAX_ITEMS    # rule items collected before the rest of a message is only scanned for skips
SHORT = 160                   # an item text longer than this is split into ask + one line of context
MAX_CHOICE = 90

WORDS = CONTRACT["ask_items"]


def yes_no() -> list[dict]:
    return [{"label": WORDS["choices"]["yes"], "value": "yes"}, {"label": WORDS["choices"]["no"], "value": "no"}]


def answer_text(item: dict, answer: str) -> str:
    """What the agent receives: it names the question, so answers given one at a time stay clear."""
    return WORDS["answer_format"].format(text=item["text"], answer=answer.strip())


def item_id(identity: str, source_at, quote: str) -> str:
    return hashlib.sha256(f"{identity}|{source_at}|{quote}".encode()).hexdigest()[:16]


def message_key(source_at, original: str):
    """What identifies the message in an item id: its time, else (no time known) its text,
    so the same quote in a later message is a new item."""
    return source_at if source_at is not None else "text:" + hashlib.sha256(original.encode()).hexdigest()[:16]


# --------------------------------------------------------------------- text helpers

# Bounded, so a message of only "[" characters stays linear (review-096 S1).
_MD_LINK = re.compile(r"\[([^\]\n]{1,200})\]\([^)\n]{0,500}\)")


def plain(text: str) -> str:
    """Markdown to one plain line: no **, __, `, link targets or list markers."""
    text = text or ""
    if "](" in text:
        text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of sentences in one unit of text."""
    spans, start = [], 0
    for match in re.finditer(r"[.!?](?=[\s)\"'*_]*(?:\s|$))", text):
        end = match.end()
        while end < len(text) and text[end] in ")\"'*_":
            end += 1
        if text[start:end].strip():
            spans.append((start, end))
        start = end
    if text[start:].strip():
        spans.append((start, len(text)))
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        out.append((s, e))
    return out


# Who a line is for. "for Alex", "for you", "for Alex or Grok Bot" count as Alex.
_FOR = r"(?:for|to)\s+(?P<who>[\w@][\w@ .-]{0,40}?)"
_LABEL = re.compile(
    r"(?i)^(?P<pre>[\s>*_#]*)(?P<label>(?:open\s+)?(?:decisions?|questions?|action\s+points?|actions?|asks?|choices?)\s+"
    + _FOR + r"|(?:decision|input|answer)\s+needed|needs?\s+your\s+(?:decision|input|answer|go-ahead|go|ok|okay|approval|sign-off)|your\s+(?:decision|call)"
    # v0.9.8.1: "One thing for you to decide tomorrow, not now:", "A question for you:"
    r"|(?:one|a|another|the|1)\s+(?:more\s+|last\s+)?(?:thing|item|point|question|decision)s?\s+for\s+you(?:\s+to\s+[a-z]+)?(?:[\s,]+[^:*\n]{0,40}?)?)"
    r"(?P<post>\s*[*_]*\s*(?::\s*[*_]*\s*|$))")
# "For Grok Bot: …" (a capitalised name, so "for example:" is not one).
_OTHER = re.compile(r"^[\s*_]*[Ff]or\s+(?P<who>[A-Z@][\w@-]*(?:\s+[A-Z][\w-]*)?)\s*[*_]*\s*:")
_ALEX = re.compile(r"(?i)^(?:alex|you|me|the owner)\b")
_ADDRESSED = re.compile(r"(?i)\b(?:you|your|yours|alex)\b|^(?:should|shall|may|can|could)\s+(?:i|we)\b|\b(?:want me to|ok(?:ay)? (?:to|if))\b")
_OFFER = re.compile(r"(?i)\b(?:say|tell me|let me know|confirm)\s+(?:if|whether)\s+you(?:'d|\s+would)?\s+"
                    r"(?:want|would like|like|prefer|rather|agree|disagree|object|are ok|are okay)\b|\b(?:say|tell me|let me know)\s+which\b")
# v0.9.8.1: statements that put a decision to Alex without a question mark ("needs your go", "your call.").
_ASK_PHRASE = re.compile(r"(?i)\b(?:needs?|need|waiting (?:for|on)|awaiting)\s+your\s+(?:go(?:-ahead)?|ok(?:ay)?|approval|sign-off|decision|input|answer|call)\b"
                         r"|\b(?:it'?s|that'?s|this is)\s+your\s+call\b|\byour\s+call\s*[:.?!]|\bfor\s+you\s+to\s+(?:decide|confirm|approve|choose|pick)\b(?!\s+from\b)|\bup\s+to\s+you\b")
# A worker's question to its coordinator, quoted in the message ("rel-1210: question: …"): not for Alex.
# v0.9.8.2: "Check its contract first. Is it the same family as X?": a question someone will look
# into (the agent or a worker it briefed), not one put to Alex. Only when the question has no "you".
_CHECK_LEAD = re.compile(r"(?i)^[\s*_`>-]*(?:\*\*[^*]{1,60}\*\*\s*|[^:.?!\n]{1,60}:\s*)?(?:then\s+|first\s+)?(?:check|verify|find out|work out|determine|"
                         r"establish|investigate|look into|figure out|ask (?:it|them|him|her|the \w+))\b")
_WORKER_ASK = re.compile(r"(?i)^[\s>*_`]*[\w.-]{1,40}[`*_]*\s*:\s*(?:question|q|ask|blocked)\s*:")
# v0.9.8.1: the agent says it can wait. Shown as a quiet "for later" badge; never a phone alert.
_LATER = re.compile(r"(?i)\b(?:tomorrow|not now|not urgent|no rush|no hurry|can wait|for later|later today|later this week|next week|"
                    r"when you(?:'re| are)? (?:back|free)|when you have (?:a )?(?:moment|minute|time)|whenever (?:you|it suits)|after the demo)\b")
_YES_NO = re.compile(r"(?i)\byes\s*(?:/|or)\s*no\b|\(y/n\)")
_AUX = re.compile(r"(?i)^(?:should|shall|can|could|may|do|does|did|is|are|was|were|will|would|want|have|has|ok(?:ay)?|any objection)\b")
_SOFT = re.compile(r"(?i)\b(?:let me know|if you (?:want|prefer|like)|please (?:review|check|advise|decide)|"
                   r"waiting for your|awaiting your|need your (?:input|decision))\b")
_LIST = re.compile(r"^(?P<indent>\s*)(?P<marker>(?:[-*+•]|\d{1,2}[.)]))\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _for_alex(who: str | None) -> bool:
    return who is None or bool(_ALEX.match(who.strip(" *_:")))


def units(text: str) -> list[dict]:
    """Paragraphs, list items and headings with their spans, outside code fences,
    quoted lines (>) and tables. Each list item is its own unit."""
    out: list[dict] = []
    current = None
    fenced = False
    offset = 0
    for line in text.splitlines(keepends=True):
        start, raw = offset, line.rstrip("\r\n")
        offset += len(line)
        if _FENCE.match(raw):
            fenced = not fenced
            current = None
            continue
        stripped = raw.strip()
        if fenced or not stripped or stripped.startswith(">") or stripped.startswith("|"):
            current = None
            continue
        lead = len(raw) - len(raw.lstrip())
        marker = _LIST.match(raw)
        heading = stripped.startswith("#")
        if marker or heading or current is None or current["heading"]:
            body = start + (marker.end() if marker else lead)
            current = {"start": body, "end": start + len(raw), "list": bool(marker),
                       "numbered": bool(marker and marker["marker"][0].isdigit()), "heading": heading}
            out.append(current)
        else:
            current["end"] = start + len(raw)
    for unit in out:
        unit["text"] = text[unit["start"]:unit["end"]]
    return out


# --------------------------------------------------------------------- choices

def _option(text: str) -> dict | None:
    label = _cap(plain(text).strip(" ,;:.?"))
    if not label or len(label) > MAX_CHOICE:
        return None
    return {"label": label, "value": label}


def choices_for(body: str, kind: str) -> list[dict]:
    """Only choices the text itself offers."""
    flat = plain(body)
    # A) … B) … (C) …), (a) … (b) …, Option A: … Option B: …
    marks = list(re.finditer(r"(?:(?<![\w])\(?([A-Da-d])\)|\bOption\s+([A-D])\s*[:.)-])\s*", flat))
    letters = [(m.group(1) or m.group(2)).upper() for m in marks]
    if len(marks) >= 2 and letters[:2] == ["A", "B"]:
        options = []
        for i, mark in enumerate(marks):
            if letters[i] != "ABCD"[i]:
                break
            end = marks[i + 1].start() if i + 1 < len(marks) else len(flat)
            chunk = re.sub(r"(?i)[\s,;]*(?:\bor\b)?[\s,;]*$", "", flat[mark.end():end])
            chunk = re.split(r"[?.;]\s", chunk + " ", maxsplit=1)[0].strip(" ?.;,")
            option = _option(f"{letters[i]}) {chunk}") if chunk else None
            if not option:
                return []
            options.append(option)
        if len(options) >= 2:
            return options
    # "X, or Y." at the start of a decision ("merge it into main, or leave it.")
    match = re.match(r"(?P<a>[^,;?]{3,100}?),\s+or\s+(?P<b>[^,;.?]{2,60})[.;?](?:\s|$)", flat)
    if match and kind == "decision":
        a, b = _option(match["a"]), _option(match["b"])
        if a and b:
            return [a, b]
    question = next((flat[s:e] for s, e in sentences(flat) if flat[s:e].rstrip(" *_").endswith("?")), "")
    # "Should I X or Y?"
    match = re.match(r"(?i)(?:should|shall)\s+(?:i|we)\s+(?P<a>[^,?]{2,60}?)\s+or\s+(?P<b>[^,?]{2,60}?)\s*\?", question)
    if match and match["b"].lower() not in ("not", "no"):
        a, b = _option(match["a"]), _option(match["b"])
        if a and b:
            return [a, b]
    # v0.9.8.1: "I plan to A … Say if you would rather I B." -> A / B
    rather = re.search(r"(?i)\b(?:say|tell me|let me know)\s+(?:if|whether)\s+you(?:'d|\s+would)?\s+(?:rather|prefer)\s+(?:i|we)\s+(?P<b>[^.?!;]{3,90})", flat)
    if rather:
        plan = None
        for plan in re.finditer(r"(?i)\b(?:i|we)(?:'m|\s+am|\s+are)?\s+(?:plan|planning|intend|propose|going|want|would like)\s+to\s+(?P<a>[^.?!;]{3,160})", flat[:rather.start()]):
            pass
        if plan:
            a = re.split(r"(?i)\s+(?:if|when|unless|once|so that)\s+|,\s", plan["a"], maxsplit=1)[0]
            first, second = _option(a), _option(rather["b"])
            if first and second:
                return [first, second]
    if _YES_NO.search(flat) or _OFFER.search(flat):
        return yes_no()
    if question and _AUX.match(question) and not re.search(r"(?i)\bor\b(?!\s+not\b)", question):
        return yes_no()
    return []


# --------------------------------------------------------------------- extraction

def _wording(body: str) -> tuple[str, str | None]:
    """A short plain question and one line of context, both from the item's own text."""
    flat = plain(body)
    if len(flat) <= SHORT:
        return _cap(flat), None
    spans = [flat[s:e].strip() for s, e in sentences(flat)]
    if len(spans) < 2:
        return _cap(flat), None
    ask = next((i for i, s in enumerate(spans) if s.rstrip(" *_").endswith("?")), None)
    if ask is None:
        ask = next((i for i, s in enumerate(spans) if _OFFER.search(s) or _SOFT.search(s) or re.search(r",\s+or\s+", s)), len(spans) - 1)
    context = spans[ask - 1] if ask > 0 else spans[ask + 1]
    return _cap(spans[ask]), _cap(context)


def _item(original: str, start: int, end: int, body: str, kind: str, rule: str, around: str | None = None) -> dict:
    text, context = _wording(body)
    choices = choices_for(body, kind)
    if len(choices) == 2 and choices[0]["value"] != "yes" and re.search(r"(?i)\byou(?:'d|\s+would)?\s+(?:rather|prefer)\s+(?:i|we)\b", plain(body)):
        # "I plan to A … say if you would rather I B" reads best as the choice itself.
        asked = f"{choices[0]['label']}, or {choices[1]['label'][:1].lower()}{choices[1]['label'][1:]}?"
        if len(asked) <= SHORT:
            first = next((plain(body)[a:b].strip() for a, b in sentences(plain(body))), "")
            context = _cap(first) if first and not first.lower().startswith(("i plan", "we plan", "say if")) else context
            text = asked
    return {"start": start, "end": end, "quote": original[start:end], "text": text, "context": context,
            "choices": choices, "kind": kind, "rule": rule, "urgency": urgency_of(around or body)}


def urgency_of(text: str) -> str | None:
    """"later" when the agent says the ask can wait ("tomorrow, not now"), else None (normal)."""
    return "later" if _LATER.search(plain(text or "")) else None


def _kind_of(label: str) -> str:
    return "question" if re.search(r"(?i)question|ask|answer|input", label) else "decision"


def rule_items(original: str, skipped: list | None = None) -> tuple[list[dict], list[str]]:
    """(items, maybe-reasons) from the rules alone. ``skipped`` collects the spans of
    text addressed to someone else."""
    items: list[dict] = []
    maybe: list[str] = []
    skipped = [] if skipped is None else skipped
    section = None          # a label line ("**Questions for Alex**") over the list or paragraph below it
    for unit in units(original):
        body = unit["text"]
        full = len(items) >= RULE_LIMIT        # enough items: only the skipped spans still matter
        if _WORKER_ASK.match(body):                          # "rel-1210: question: …" is worker -> coordinator
            section = None
            skipped.append((unit["start"], unit["end"]))
            continue
        other = _OTHER.match(body)
        if other and not _for_alex(other["who"]):
            section = None
            skipped.append((unit["start"], unit["end"]))
            continue                                         # "For Grok Bot: …" is not for Alex
        label = _LABEL.match(body)
        if label:
            alex = _for_alex(label.groupdict().get("who"))
            rest = body[label.end():]
            if rest.strip() and not unit["heading"]:         # "Decision for Alex: …" on one line
                if alex and not full:
                    items.append(_item(original, unit["start"], unit["end"], rest, _kind_of(label["label"]), "label", around=body))
                elif not alex:
                    skipped.append((unit["start"], unit["end"]))
                if not unit["list"]:
                    section = None
                continue
            section = {"kind": _kind_of(label["label"]), "alex": alex, "used": False, "label": body}
            continue
        if section and (unit["list"] or not section["used"]) and not unit["heading"]:
            if not section["alex"]:
                skipped.append((unit["start"], unit["end"]))
            elif not full:
                items.append(_item(original, unit["start"], unit["end"], body, section["kind"], "section",
                                   around=body + " " + section.get("label", "")))
            section["used"] = True
            if not unit["list"]:
                section = None
            continue
        section = None
        if unit["heading"] or full:
            continue
        before, last_raw = "", ""   # the sentence before this one, plain and raw (v0.9.8.2)
        for s, e in sentences(body):
            if len(items) >= RULE_LIMIT:
                break
            sentence = body[s:e]
            clean = plain(sentence)
            prev_clean, before = before, clean
            prev_raw, last_raw = (last_raw if s else ""), sentence
            span = (unit["start"] + s, unit["start"] + e)
            if clean.rstrip(" *_").endswith("?"):
                if prev_clean and _CHECK_LEAD.search(prev_clean) and not re.search(r"(?i)\b(?:you|your|alex)\b", clean):
                    if len(maybe) < 3:
                        maybe.append(f"a question to look into, not for you: {clean[:80]}")
                    continue
                if unit["numbered"] or _ADDRESSED.search(clean):
                    items.append(_item(original, *span, sentence, "question", "question mark", around=body))
                elif len(maybe) < 3:
                    maybe.append(f"a question not addressed to you: {clean[:80]}")
            elif _OFFER.search(clean):
                # "I plan to A. Say if you would rather I B.": the plan is part of the decision.
                lead = prev_raw if prev_raw and re.search(r"(?i)\b(?:i|we)(?:'m|\s+am|\s+are)?\s+(?:plan|planning|intend|propose|going)\s+to\b", prev_raw) else ""
                if lead:
                    a0 = unit["start"] + body.index(lead)
                    items.append(_item(original, a0, span[1], lead + " " + sentence, "decision", "offer", around=body))
                else:
                    items.append(_item(original, *span, sentence, "decision", "offer", around=body))
            elif _ASK_PHRASE.search(clean):
                items.append(_item(original, *span, sentence, "decision", "ask phrase", around=body))
            elif _SOFT.search(clean) and len(maybe) < 3:
                maybe.append(f"it may want something: {clean[:80]}")
    return items, maybe


@lru_cache(maxsize=512)
def not_for_alex(text: str) -> frozenset[int]:
    """Line numbers (0-based) that do not ask Alex anything: code, quoted lines, tables,
    and text addressed to someone else. The tier rule (attention.question_text) skips
    them too, so the tier and the items agree. Cached per message text."""
    from bisect import bisect_left, bisect_right
    out, fenced, starts, ends, offset = set(), False, [], [], 0
    for i, line in enumerate(text.splitlines(keepends=True)):
        starts.append(offset)
        ends.append(offset + len(line.rstrip("\r\n")))
        offset += len(line)
        raw = line.strip()
        if _FENCE.match(line):
            fenced = not fenced
            out.add(i)
        elif fenced or raw.startswith(">") or raw.startswith("|"):
            out.add(i)
    skipped: list = []
    rule_items(text, skipped)
    for span_start, span_end in skipped:            # the lines a span touches, found by their ends once
        out.update(range(bisect_left(ends, span_start), bisect_right(starts, span_end)))
    return frozenset(out)


def _direct_ask(original: str) -> dict | None:
    """The line the tier rules quote as a direct ask, so the tier and the items agree."""
    from .attention import question_text
    found = question_text(original)
    if not found:
        return None
    # A line over 500 characters comes back cut ("…"): match the line by its uncut start;
    # the quote is still the whole line, word for word (review-096 B1).
    cut = found.endswith("…")
    head = found[:-1].rstrip() if cut else found
    for line_match in re.finditer(r"(?m)^.*$", original):
        line = line_match.group(0)
        core = re.sub(r"^[>*#\s]+", "", line.strip().strip("│┃ "))
        flat = re.sub(r"\s+", " ", core)
        if core and (flat.startswith(head) if cut else flat == found):
            start = line_match.start() + line.index(core)
            return _item(original, start, start + len(core), core, "question", "direct ask")
    return None


def _overlaps(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def _find(original: str, quote: str) -> tuple[int, int] | None:
    """The span of a quote in the original; whitespace may differ, nothing else."""
    quote = (quote or "").strip()
    if len(quote) < 8:
        return None
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    match = re.search(pattern, original)
    return (match.start(), match.end()) if match else None


def _ai_items(original: str, ai: dict | None, rules: list[dict]) -> list[dict]:
    """AI wording for rule items, and AI items whose quote is in the original."""
    out = []
    for entry in (ai or {}).get("asks") or []:
        if not isinstance(entry, dict):
            continue
        span = _find(original, str(entry.get("quote") or ""))
        text = plain(str(entry.get("text") or ""))[:200]
        if not span or not text:
            continue
        context = plain(str(entry.get("context") or ""))[:240] or None
        match = next((r for r in rules if r["start"] < span[1] and span[0] < r["end"]), None)
        if match:
            match.update(text=text, context=context, rule=match["rule"] + " + AI wording")
            continue
        kind = entry.get("kind") if entry.get("kind") in ("decision", "question") else "question"
        quote = original[span[0]:span[1]]
        choices = choices_for(quote, kind)
        urgent = entry.get("urgency") if entry.get("urgency") in ("later", "now") else None
        out.append({"start": span[0], "end": span[1], "quote": quote, "text": text, "context": context,
                    "choices": choices, "kind": kind, "rule": "AI, quote checked",
                    "urgency": "later" if urgent == "later" else urgency_of(quote + " " + (context or ""))})
    return out


def summary_of(original: str, ai: dict | None = None) -> str | None:
    headline = plain(str((ai or {}).get("headline") or ""))
    if headline:
        return headline[:300]
    for unit in units(original):
        if unit["heading"] or unit["list"] or _LABEL.match(unit["text"]):
            continue
        flat = re.sub(r"\s+", " ", unit["text"]).strip()
        spans = sentences(flat)[:2]
        text = flat[:spans[-1][1]] if spans else flat
        if len(text) > 300:
            text = (flat[:spans[0][1]] if spans and spans[0][1] <= 300 else flat[:300].rsplit(" ", 1)[0] + "…")
        return text or None
    return None


def extract(original: str, *, ai: dict | None = None) -> dict:
    """{"state", "summary", "items"} for one message. Items have no ids or people yet.
    Cached by text: the view is built often and the message rarely changes."""
    found = _extract((original or "")[:MAX_ORIGINAL], json.dumps(ai, sort_keys=True, default=str) if ai else "")
    return {**found, "items": [dict(i, choices=[dict(c) for c in i["choices"]]) for i in found["items"]],
            "maybe": list(found["maybe"])}


@lru_cache(maxsize=512)
def _extract(original: str, ai_json: str) -> dict:
    ai = json.loads(ai_json) if ai_json else None
    items, maybe = rule_items(original)
    direct = _direct_ask(original)
    if direct and not any(_overlaps(direct, i) for i in items):
        items.append(direct)
    items.sort(key=lambda i: i["start"])
    kept: list[dict] = []
    for item in items:                     # one item per span: the first (widest rule) wins
        if not any(_overlaps(item, k) for k in kept):
            kept.append(item)
    kept += _ai_items(original, ai, kept)
    kept.sort(key=lambda i: i["start"])
    kept = kept[:MAX_ITEMS]
    needs = (ai or {}).get("needs_you") or {}
    if not kept and needs.get("kind") in ("approval", "question", "decision") and needs.get("ask"):
        maybe.append("the AI summary records an ask, but no quote for it was found")
    kept = [i for i in kept if original[i["start"]:i["end"]] == i["quote"]]   # never a quote that is not there
    state = "questions" if kept else "maybe" if maybe else "none"
    return {"state": state, "summary": summary_of(original, ai), "items": kept, "maybe": maybe[:3]}


# --------------------------------------------------------------------- agent.asks

STALE_SECONDS = 600     # a message this much older than its event is from an earlier turn


def _ai_of(agent: dict) -> dict | None:
    """The AI summary that already ran for this agent's current item, if any."""
    summary = agent.get("summary") or {}
    if summary.get("status") != "complete" or not summary.get("current"):
        return None
    return {"headline": summary.get("headline"), "needs_you": summary.get("needs_you"), "asks": summary.get("asks")}


def _public(identity: str, source_at, item: dict, who: dict, answers: dict) -> dict:
    key = item_id(identity, source_at, item["quote"])     # source_at: message_key() of the message
    return {"id": key, "text": item["text"], "context": item["context"], "quote": item["quote"],
            "choices": item["choices"], "kind": item["kind"], "asked_by": who, "answer_to": None,
            "answered": answers.get(key), "urgency": item.get("urgency")}


_SPACE = re.compile(r"[^a-z0-9]+")


def _norm_text(text: str) -> str:
    return _SPACE.sub(" ", (text or "").lower()).strip()


_STOP = frozenset("a an the to of and or for on in is it i we you your me my this that be do if so as at by".split())


def _words(text: str) -> frozenset[str]:
    return frozenset(w for w in _norm_text(text).split() if w not in _STOP)


def same_ask(a: str, b: str) -> bool:
    """v0.9.8.2: the same ask, also when reworded a little (a summary, a repeat in a later
    message): same words after normalising, one inside the other, 80% of all words shared, or
    85% of the shorter one's words (at least 5) in the other."""
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return False
    if na == nb or (min(len(na), len(nb)) >= 24 and (na in nb or nb in na)):
        return True
    wa, wb = _words(a), _words(b)
    if not (wa and wb):
        return False
    shared = len(wa & wb)
    # Also "Reminder: <ask>" and "Still waiting on this: <ask>": nearly all of the shorter one's words.
    return shared / len(wa | wb) >= 0.8 or (min(len(wa), len(wb)) >= 5 and shared / min(len(wa), len(wb)) >= 0.85)


def _dedupe(items: list[dict]) -> list[dict]:
    """Newest first in, one item per ask out (newest wording kept, original order). An answer
    on any copy stays on the kept one, so an answered ask does not come back as open."""
    kept: list[dict] = []
    for item in items:
        twin = next((k for k in kept if k.get("kind") == item.get("kind") == "permission" and k["id"] == item["id"]
                     or same_ask(k["text"], item["text"]) or (item.get("quote") and same_ask(k.get("quote") or "", item["quote"]))), None)
        if twin is None:
            kept.append(item)
        elif item.get("answered") and not twin.get("answered"):
            twin["answered"] = item["answered"]
    return kept


_ANSWER_RE = None


def is_item_answer(text: str) -> bool:
    """v0.9.8.6: Alex's turn is only answers to question cards (ui_contract ask_items.answer_format,
    'Answer to "<ask>": <answer>', one per line), not a free message to the agent."""
    global _ANSWER_RE
    if _ANSWER_RE is None:
        fmt = CONTRACT["ask_items"]["answer_format"]
        head, rest = fmt.split("{text}", 1)
        mid = rest.split("{answer}", 1)[0]
        _ANSWER_RE = re.compile(re.escape(head) + r".+?" + re.escape(mid) + r".*")
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    return bool(lines) and bool(_ANSWER_RE.fullmatch(lines[0])) and all(
        _ANSWER_RE.fullmatch(line) or not line.lower().startswith("answer to") for line in lines)


def earlier_items(agent: dict, earlier: list[dict] | None, who: dict, answers: dict, seen=None) -> list[dict]:
    """v0.9.8.1: rule items from the agent's earlier messages since Alex last wrote to it.

    A coordinator answers its workers between Alex's turns, so an ask in a status report is
    often followed by more messages within seconds; it must not disappear with them. Rules
    only (no AI), each item keyed to its own message; same text as a later item = one item."""
    out = []
    for msg in earlier or []:
        text = (msg.get("text") or "")[:MAX_ORIGINAL]
        if not text.strip():
            continue
        found = extract(text)
        for item in found["items"]:
            public = _public(agent["identity"], message_key(msg.get("at"), text), item, who, answers)
            public.update(earlier=True, source_at=msg.get("at"))
            out.append(public)
    return out


def add_report_asks(agent: dict, report_asks: list[dict], messages: list[dict], answers: dict) -> int:
    """v0.9.8.1: asks the coordinator report's model found (report_asks) become items when their
    quote is found word for word in one of the agent's messages since Alex last wrote. Returns
    how many were added. Same span or same text as an item already there = not added."""
    from .content import short_name
    who = {"identity": agent["identity"], "name": short_name(agent)}
    asks = agent.get("asks") or {"state": "none", "source_at": None, "summary": None, "original": "", "items": []}
    items = list(asks.get("items") or [])
    added = 0
    for entry in report_asks or []:
        for msg in messages or []:
            text = (msg.get("text") or "")[:MAX_ORIGINAL]
            span = _find(text, entry.get("quote") or "")
            if not span:
                continue
            quote = text[span[0]:span[1]]
            if any(same_ask(entry.get("text") or "", i["text"]) or _norm_text(quote) in _norm_text(i.get("quote") or "") or
                   _norm_text(i.get("quote") or "") in _norm_text(quote) for i in items):
                break
            options = [_option(o) for o in entry.get("options") or []]
            options = [o for o in options if o] or choices_for(quote, "decision")
            item = {"quote": quote, "text": plain(entry["text"])[:200], "context": None, "choices": options,
                    "kind": "decision", "urgency": "later" if entry.get("urgency") == "later" else urgency_of(quote)}
            public = _public(agent["identity"], message_key(msg.get("at"), text), item, who, answers)
            public.update(source_at=msg.get("at"), from_report=True)
            items.append(public)
            added += 1
            break
    if added:
        agent["asks"] = dict(asks, state="questions", items=items[-MAX_ITEMS:])
    return added


def for_agent(agent: dict, message: str | None, coordinator: dict | None, answers: dict,
              earlier: list[dict] | None = None) -> dict | None:
    """agent.asks for one agent of the view, or None when nothing in it may need Alex.

    ``message`` is the full last message (the engine keeps it; the view's copy stops at
    2000 characters). A worker with a registered lead is answered by that lead, so its
    messages give no items for Alex; a terminal dialog only a human can answer does."""
    from .content import short_name
    if agent.get("acked") or not agent.get("present", True) or agent.get("session_collision"):
        return None
    who = {"identity": agent["identity"], "name": short_name(agent)}
    question = agent.get("question") or {}
    if question.get("terminal"):          # unchanged from 0.9.5: one item with the dialog's own choices
        original = agent.get("prompt_text") or question.get("text") or ""
        span = _find(original, question.get("text") or "")
        if not span:
            original = question.get("text") or ""
            span = (0, len(original))
        item = {"quote": original[span[0]:span[1]], "text": plain(question.get("text") or ""), "context": None,
                "choices": [{"label": o, "value": o} for o in question.get("options") or []], "kind": "permission"}
        return {"state": "questions", "source_at": question.get("at"), "summary": None, "original": original,
                "items": [_public(agent["identity"], message_key(question.get("at"), original), item, who, answers)]}
    if coordinator and not coordinator.get("inferred"):
        return None
    last = agent.get("last_assistant") or {}
    current = None
    original = (message or last.get("text") or "")[:MAX_ORIGINAL]
    if agent.get("status") in ("idle", "done") and last.get("last_role") != "user" and original.strip():
        found = extract(original, ai=_ai_of(agent))
        if found["state"] == "questions" or agent.get("tier"):
            source_at = last.get("at")
            current = {"state": found["state"], "source_at": source_at, "summary": found["summary"], "original": original,
                       "items": [_public(agent["identity"], message_key(source_at, original), i, who, answers) for i in found["items"]]}
    extra = earlier_items(agent, earlier, who, answers)
    if current and current.get("items"):
        current["items"] = _dedupe(list(current["items"]))
    if not extra:
        return current
    if current is None:
        current = {"state": "questions", "source_at": last.get("at"), "summary": None, "original": original, "items": []}
    # Newest first into _dedupe: the last message, then earlier ones from the newest back.
    newest_first = list(current["items"]) + sorted(extra, key=lambda i: -(i.get("source_at") or 0))
    keep = {id(i) for i in _dedupe(newest_first)}
    current["items"] = [i for i in extra + current["items"] if id(i) in keep][-MAX_ITEMS:]
    current["state"] = "questions"
    return current


def rule_state(agent: dict | None, event_at: float | None = None) -> str | None:
    """The rules' state of an agent's current message, for the tier cap (no AI, so the
    phone and the app decide alike). None when there is no finished message to judge."""
    if not agent or agent.get("status") not in ("idle", "done"):
        return None
    last = agent.get("last_assistant") or {}
    if last.get("last_role") == "user":
        return None
    if event_at and last.get("at") and last["at"] < event_at - STALE_SECONDS:
        return None                       # the transcript has not caught up with this event yet
    message = agent.get("last_message") or (agent.get("asks") or {}).get("original") or last.get("text")
    if not message or not message.strip():
        return None
    from .attention import question_text
    found = extract(message)
    # v0.9.8.1: every ask says it can wait ("tomorrow, not now"): a quiet "for later" item, not a phone alert.
    if found["state"] == "questions" and all(i.get("urgency") == "later" for i in found["items"]):
        return "later"
    if question_text(message):
        return None                       # the tier rule quoted a direct ask: never lower (review-096 B1)
    return found["state"]


# --------------------------------------------------------------------- answers

# Table ask_answers (store.py): item_id, identity (the agent that asked), text (Alex's answer
# as he gave it), at, reply_id (replies.id of the delivery).
DELIVERY = {"queued": "queued", "sending": "queued", "sent": "sent", "failed": "failed", "cancelled": "failed"}


def load_answers(store) -> dict:
    """item id -> {"text", "at", "delivery"}; kept over a restart (SQLite)."""
    rows = store._exec("SELECT item_id, text, at, reply_id FROM ask_answers").fetchall()
    status = {}
    ids = [r[3] for r in rows if r[3] is not None]
    if ids:
        try:
            marks = ",".join("?" * len(ids))
            status = dict(store._exec(f"SELECT id, status FROM replies WHERE id IN ({marks})", ids).fetchall())
        except Exception:      # no replies table (engine used alone in a test)
            status = {}
    return {item: {"text": text, "at": at, "delivery": DELIVERY.get(status.get(reply), "sent" if reply is None else "queued")}
            for item, text, at, reply in rows}


def save_answer(store, item: str, identity: str, text: str, at: float, reply_id: int | None) -> None:
    store._exec("INSERT OR REPLACE INTO ask_answers (item_id, identity, text, at, reply_id) VALUES (?, ?, ?, ?, ?)",
                (item, identity, text, at, reply_id))


def is_open(item: dict) -> bool:
    """Unanswered, or the answer failed (Alex can answer again)."""
    return not item.get("answered") or item["answered"].get("delivery") == "failed"


# --------------------------------------------------------------------- the view

def decorate(view: dict) -> None:
    """After reply routing: who each answer goes to, the dialog image, and the same
    items on view.questions (0.9.5 clients)."""
    by_id = {a["identity"]: a for a in view["agents"]}
    shown: set[str] = set()
    for agent in view["agents"]:
        # v0.9.8.2: an in-process subagent card is a copy of its parent's; the parent asks, once.
        if agent.get("in_process") or "/subagent/" in agent["identity"]:
            agent["asks"] = None
        asks = agent.get("asks")
        if not asks:
            continue
        mine = [i for i in asks.get("items") or [] if i["id"] not in shown]
        if len(mine) != len(asks.get("items") or []):
            asks = agent["asks"] = dict(asks, items=mine)
            if not mine and asks.get("state") == "questions":
                agent["asks"] = None
                continue
        shown.update(i["id"] for i in mine)
        target = agent.get("reply_target") or {}
        if target.get("identity"):
            where = " / ".join(x for x in (target.get("workspace_label"), target.get("tab_label")) if x) or None
            answer_to = {"identity": target["identity"], "name": target.get("name"), "where": where}
        else:
            answer_to = {"identity": None, "name": None, "where": None,
                         "error": target.get("error") or "The recipient is unavailable."}
        image = (agent.get("question") or {}).get("image")
        for item in asks["items"]:
            item["answer_to"] = dict(answer_to)
            if item["kind"] == "permission" and image:
                item["image"] = image
    for question in view.get("questions") or []:
        asks = (by_id.get(question.get("identity")) or {}).get("asks")
        if asks and asks.get("items"):
            question["items"] = asks["items"]


def find(view: dict, item: str) -> tuple[dict | None, dict | None]:
    for agent in view["agents"]:
        for entry in (agent.get("asks") or {}).get("items") or []:
            if entry["id"] == item:
                return agent, entry
    return None, None
