"""Process-scoped model launches for summaries and the notification gate.

Homes are set only in the child environment. This never changes a global
default account, never passes access tokens, and never uses an
account-switching wrapper.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CODEX_MODELS = (("gpt-6.2-sol", "Sol 6.2"), ("gpt-6.1-sol", "Sol 6.1"))   # 6.2 default; 6.1 kept so older saved choices still validate
CLAUDE_MODELS = (("sonnet", "Sonnet"), ("opus", "Opus"), ("haiku", "Haiku"))
EFFORTS = ("low", "medium", "high")
ACCOUNT_IDS = ("codex-personal", "codex-work", "claude-personal", "claude-main")

# Dropped from the child environment so a leftover shell token cannot
# silently change which Codex or Claude account is used.
_SECRET_ENV = (
    "CODEX_ACCESS_TOKEN", "CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_ACCESS_TOKEN",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
    "OMARCHY_AGENT_CODEX_HOME",
)


@dataclass(frozen=True)
class Account:
    id: str
    provider: str
    label: str
    home_env: str
    home: str
    models: tuple[tuple[str, str], ...]
    efforts: tuple[str, ...]
    default_model: str

    @property
    def available(self) -> bool:
        return Path(self.home).expanduser().is_dir()


def _path(settings, key: str, default: Path) -> str:
    if settings is not None:
        value = settings.get(key)
        if value:
            return str(Path(os.path.expanduser(str(value))))
    return str(default)


def account_catalog(settings=None) -> list[Account]:
    home = Path.home()
    return [
        Account("codex-personal", "codex", "Codex · Personal", "CODEX_HOME",
                _path(settings, "summaries.codex_personal_home", home / ".codex-personal"),
                CODEX_MODELS, EFFORTS, "gpt-6.2-sol"),
        Account("codex-work", "codex", "Codex · Work", "CODEX_HOME",
                _path(settings, "summaries.codex_work_home", home / ".codex"),
                CODEX_MODELS, EFFORTS, "gpt-6.2-sol"),
        Account("claude-personal", "claude", "Claude · Personal", "CLAUDE_CONFIG_DIR",
                _path(settings, "summaries.claude_config_dir", home / ".claude-personal"),
                CLAUDE_MODELS, EFFORTS, "sonnet"),
        Account("claude-main", "claude", "Claude · Main", "CLAUDE_CONFIG_DIR",
                _path(settings, "summaries.claude_main_home", home / ".claude"),
                CLAUDE_MODELS, EFFORTS, "sonnet"),
    ]


def resolve_account(settings, dotted: str = "summaries.account") -> Account:
    catalog = account_catalog(settings)
    wanted = str((settings.get(dotted) if settings is not None else None) or "codex-personal")
    for account in catalog:
        if account.id == wanted:
            return account
    return catalog[0]


def public_accounts(settings=None) -> list[dict]:
    return [
        {
            "id": account.id,
            "label": account.label,
            "provider": account.provider,
            "available": account.available,
            "models": [{"id": mid, "label": label} for mid, label in account.models],
            "efforts": list(account.efforts),
            "default_model": account.default_model,
        }
        for account in account_catalog(settings)
    ]


def find_claude(configured: str | None = "") -> str | None:
    """The real Claude Code binary, never the account-picking wrapper."""
    candidates = [configured] if configured else []
    candidates += [os.path.expanduser("~/.local/share/mise/installs/claude/latest/claude"),
                   os.path.expanduser("~/.local/bin/claude"), shutil.which("claude")]
    for path in candidates:
        if not path:
            continue
        real = os.path.realpath(os.path.expanduser(path))
        if os.path.isfile(real) and os.access(real, os.X_OK) and "claude-accounts" not in real:
            try:
                with open(real, "rb") as fh:
                    shebang = fh.read(2) == b"#!"
                if shebang and "claude-account" in Path(real).read_text(errors="ignore")[:4000]:
                    continue
            except OSError:
                continue
            return real
    return None


def find_codex(configured: str | None = "") -> str | None:
    """The real Codex binary, never an account-switching wrapper."""
    candidates = [configured] if configured else []
    home = Path.home()
    candidates += [
        str(home / ".local/share/mise/installs/codex/latest/bin/codex"),
        str(home / ".local/share/mise/shims/codex"),
        str(home / ".local/bin/codex"),
        "/usr/local/bin/codex",
        "/usr/bin/codex",
        shutil.which("codex"),
    ]
    seen: set[str] = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        real = os.path.realpath(os.path.expanduser(path))
        if not os.path.isfile(real) or not os.access(real, os.X_OK):
            continue
        try:
            with open(real, "rb") as fh:
                shebang = fh.read(2) == b"#!"
            text = Path(real).read_text(errors="ignore")[:4000] if shebang else ""
        except OSError:
            continue
        if "codex-account" in text or "claude-account" in text or "Starts Claude Code as the account" in text:
            continue
        return real
    return None


def find_binary(provider: str, settings=None) -> str | None:
    if provider == "codex":
        configured = settings.get("summaries.codex_bin") if settings is not None else None
        return find_codex(configured or None)
    configured = settings.get("summaries.claude_bin") if settings is not None else None
    return find_claude(configured or None)


def clean_env(home: str, provider: str = "claude") -> dict:
    """Only what the CLI needs; nothing that could switch account or provider."""
    keep = ("HOME", "USER", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "TMPDIR")
    env = {k: os.environ[k] for k in keep if os.environ.get(k)}
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    home = os.path.expanduser(home)
    if provider == "codex":
        env["CODEX_HOME"] = home
    else:
        env["CLAUDE_CONFIG_DIR"] = home
    return env


def build_command(binary: str, model: str, effort: str, provider: str = "claude",
                  last_message: str | None = None, system_prompt: str | None = None) -> list[str]:
    if provider == "codex":
        command = [
            binary, "exec",
            "-m", model,
            "-c", f'model_reasoning_effort="{effort}"',
            "-s", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color", "never",
            "--json",
        ]
        if last_message:
            command += ["-o", last_message]
        command.append("-")  # prompt from stdin
        return command
    command = [binary, "-p", "--model", model, "--effort", effort, "--output-format", "json",
               "--no-session-persistence", "--tools", "", "--setting-sources", "", "--strict-mcp-config",
               "--disable-slash-commands"]
    if system_prompt:
        command += ["--system-prompt", system_prompt]
    return command


def parse_reply(raw: str) -> tuple[dict, dict]:
    """Parse a Claude Code JSON envelope (and a JSON object if that's all we got)."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("the CLI returned no output")
    envelope = None
    # Prefer the last JSON object that looks like Claude's result envelope.
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line.startswith("{") or "type" not in line and "subtype" not in line:
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and (candidate.get("type") == "result" or "subtype" in candidate):
            envelope = candidate
            break
    if envelope is None:
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("the CLI did not return JSON") from None
            try:
                envelope = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                raise ValueError("the CLI did not return JSON") from None
    if not isinstance(envelope, dict):
        raise ValueError("the CLI did not return JSON")
    if envelope.get("is_error") or envelope.get("subtype") == "error" or envelope.get("type") == "error":
        raise ValueError(f"the CLI reported an error ({(envelope.get('result') or envelope.get('error') or 'unknown')!s}"[:200])
    # Claude puts the model JSON in result (a string). A bare object is the summary itself.
    text = envelope.get("result")
    if isinstance(text, str) and text.strip():
        try:
            summary = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("the model's JSON could not be read") from None
            try:
                import re
                match = re.search(r"\{.*\}", text, re.S)
                summary = json.loads(match.group(0)) if match else None
            except (json.JSONDecodeError, AttributeError):
                summary = None
            if not isinstance(summary, dict):
                raise ValueError("the model's JSON could not be read") from None
    elif isinstance(envelope.get("headline"), (str, type(None))) and "headline" in envelope:
        summary = envelope
        text = raw
    else:
        raise ValueError("the model's JSON could not be read")
    if not isinstance(summary, dict):
        raise ValueError("the model's JSON could not be read")
    usage = {k: v for k, v in (envelope.get("usage") or {}).items() if isinstance(v, (int, float))}
    info = {"usage": usage, "resolved_model": ",".join((envelope.get("modelUsage") or {}).keys()) or None,
            "duration_ms": envelope.get("duration_ms"), "output_chars": len(text if isinstance(text, str) else raw)}
    return summary, info


def parse_codex_reply(raw: str, last_message: str = "") -> tuple[dict, dict]:
    """Parse `codex exec --json` JSONL plus the optional last-message file."""
    last = (last_message or "").strip()
    usage: dict = {}
    resolved = None
    failure = None
    if raw:
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            typ = event.get("type") or (event.get("event") or {}).get("type")
            body = event.get("event") if isinstance(event.get("event"), dict) else event
            if typ in ("error", "turn.failed"):   # v0.9.8.1: say why (e.g. the account's usage limit)
                error = body.get("error") if isinstance(body.get("error"), dict) else {}
                found = body.get("message") or error.get("message")
                if isinstance(found, str) and found.strip():
                    failure = re.sub(r"\s*\(?https?://\S+\)?", "", found).strip()
            item = body.get("item") if isinstance(body.get("item"), dict) else body
            if typ in ("turn.completed", "event") or body.get("type") == "turn.completed":
                found = body.get("usage") or event.get("usage") or {}
                if isinstance(found, dict):
                    usage = {k: v for k, v in found.items() if isinstance(v, (int, float))}
            if item.get("type") in ("agent_message", "message") and isinstance(item.get("text"), str) and item["text"].strip():
                last = item["text"]
            if isinstance(event.get("model"), str):
                resolved = event["model"]
            if isinstance(body.get("model"), str):
                resolved = body["model"]
    if not last:
        raise ValueError(f"the CLI failed: {failure[:200]}" if failure else "the CLI did not return JSON")
    # The last message should be the JSON object we asked for; accept a fenced block.
    text = last
    try:
        summary = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("the model's JSON could not be read") from None
        try:
            summary = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            raise ValueError("the model's JSON could not be read") from None
    if not isinstance(summary, dict):
        raise ValueError("the model's JSON could not be read")
    # Normalise common token key names from Codex usage.
    if "input_tokens" not in usage and "input" in usage:
        usage["input_tokens"] = usage["input"]
    if "output_tokens" not in usage and "output" in usage:
        usage["output_tokens"] = usage["output"]
    return summary, {"usage": usage, "resolved_model": resolved, "duration_ms": None, "output_chars": len(text)}


def parse_model_output(provider: str, raw: str, last_message: str = "") -> tuple[dict, dict]:
    if provider == "codex":
        return parse_codex_reply(raw, last_message)
    return parse_reply(raw)


def run_cli(command: list[str], prompt: str, env: dict, timeout: float) -> str:
    result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=timeout, env=env,
                            cwd=env.get("TMPDIR") or "/tmp")
    if result.returncode != 0 and not result.stdout.strip():
        detail = (result.stderr or "").strip().splitlines()
        # Never include environment or tokens; a short stderr reason is enough.
        reason = detail[-1][:200] if detail else f"the CLI exited with {result.returncode}"
        raise ValueError(reason)
    return result.stdout


def invoke(account: Account, model: str, effort: str, prompt: str, timeout: float,
           system_prompt: str = "", settings=None,
           runner=None) -> tuple[dict, dict]:
    """Run one isolated model call and return (parsed JSON object, meta)."""
    binary = find_binary(account.provider, settings)
    if not binary:
        raise ValueError(f"{account.provider} binary not found")
    home = os.path.expanduser(account.home)
    if not os.path.isdir(home):
        raise ValueError(f"{account.label} home not found: {home}")
    env = clean_env(home, account.provider)
    runner = runner or run_cli
    if account.provider == "codex":
        with tempfile.TemporaryDirectory(prefix="herdr-llm-") as tmp:
            last = str(Path(tmp) / "last.txt")
            command = build_command(binary, model, effort, provider="codex", last_message=last)
            body = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            raw = runner(command, body, env, timeout)
            last_text = Path(last).read_text() if Path(last).is_file() else ""
            return parse_model_output("codex", raw, last_text)
    command = build_command(binary, model, effort, provider="claude", system_prompt=system_prompt)
    raw = runner(command, prompt, env, timeout)
    return parse_model_output("claude", raw)


def default_model_for(account: Account, settings=None, dotted: str = "summaries.model") -> tuple[str, str]:
    model = (settings.get(dotted) if settings is not None else None) or account.default_model
    effort_key = dotted.rsplit(".", 1)[0] + ".effort" if dotted.endswith(".model") else "summaries.effort"
    effort = (settings.get(effort_key) if settings is not None else None) or "low"
    known = {mid for mid, _ in account.models}
    if model not in known:
        model = account.default_model
    if effort not in EFFORTS:
        effort = "low"
    return str(model), str(effort)
