#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""List the agent sessions worth considering for one day's journal, and identify the current session.

Reads Claude Code transcripts (~/.claude/projects/<cwd>/<id>.jsonl) and omp transcripts
(~/.omp/agent/sessions/<cwd>/<ts>_<id>.jsonl) directly, so a session counts on every day it was
active, not only the day it started. Standard library only.

  journal.py candidates [--date YYYY-MM-DD] [--tz NAME] [--config PATH]
      JSON: the day's note path, candidate sessions with a short digest of that day's activity,
      and the sessions filtered out by rule, each with its reason.
  journal.py paths [--date YYYY-MM-DD] [--config PATH]
      JSON: the day note and week note paths for a day, from the configured templates.
  journal.py whoami [--nonce TOKEN] [--config PATH]
      JSON: the current session's key. Claude Code exposes its id; elsewhere, pass a fresh token
      you printed in an earlier command so the script can find it in the transcript.
  journal.py config-path
      Print where the config file is read from.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROMPT_CHARS = 300
REPLY_CHARS = 600
COMMAND_CHARS = 200
MAX_PROMPTS = 12
MAX_FILES = 25
MAX_COMMANDS = 15

# Temp and scratch directories hold throwaway runs and delegated work; they never count.
# The system temp directory (tempfile.gettempdir()) is added at run time.
ALWAYS_EXCLUDED = ["/tmp", "/private/tmp", "/var/folders", "/private/var/folders"]

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "edit", "write"}
SHELL_TOOLS = {"Bash", "bash"}
# Shell commands that leave a mark outside the session. The config's notable_commands adds more.
NOTABLE_COMMANDS = [
    r"\bgit\s+(commit|push|merge|tag)\b",
    r"\bgh\s+(pr|issue|release)\b",
    r"\bglab\s+(mr|issue|release)\b",
    r"\b(npm|uv|pnpm|cargo)\s+publish\b",
]
DEFAULT_DAY_NOTE = "{date}-journal.md"
DEFAULT_WEEK_NOTE = "{week_start}-week.md"
TEMPLATE_FIELDS = "date, year, month, day, week, iso_year, week_start, week_end"
# User-role records that the harness wrote, not the person.
HARNESS_TEXT = (
    "<task-notification", "<local-command", "<system-reminder", "<bash-", "<agent-message",
    "[Request interrupted", "Caveat:",
)
HOUSEKEEPING_COMMANDS = {
    "clear", "compact", "exit", "resume", "model", "config", "cost", "context", "status", "login",
    "logout", "help", "effort", "fast", "rename", "theme", "usage", "plugin", "mcp", "doctor", "add-dir",
}
COMMAND_NAME = re.compile(r"<command-name>/?([^<]+)</command-name>")
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


class ConfigError(Exception):
    pass


@dataclass
class Config:
    journal_dir: Path
    claude_projects: Path
    omp_sessions: Path
    exclude_cwds: list[str] = field(default_factory=list)
    include_headless: bool = False
    day_note: str = DEFAULT_DAY_NOTE
    week_note: str = DEFAULT_WEEK_NOTE
    notable_commands: list[str] = field(default_factory=list)

    def notable(self) -> re.Pattern:
        return re.compile("|".join(f"(?:{p})" for p in NOTABLE_COMMANDS + self.notable_commands))


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "agent-journal" / "config.toml"


def _expand(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(f"no config at {path}; create it with at least journal_dir")
    data = tomllib.loads(path.read_text())
    if not data.get("journal_dir"):
        raise ConfigError(f"{path} must set journal_dir")
    home = Path.home()
    cfg = Config(
        journal_dir=Path(_expand(data["journal_dir"])),
        claude_projects=Path(_expand(data.get("claude_projects", str(home / ".claude/projects")))),
        omp_sessions=Path(_expand(data.get("omp_sessions", str(home / ".omp/agent/sessions")))),
        exclude_cwds=[_expand(p).rstrip("/") for p in data.get("exclude_cwds", [])],
        include_headless=bool(data.get("include_headless", False)),
        day_note=data.get("day_note", DEFAULT_DAY_NOTE),
        week_note=data.get("week_note", DEFAULT_WEEK_NOTE),
        notable_commands=list(data.get("notable_commands", [])),
    )
    for name in ("day_note", "week_note"):
        template = getattr(cfg, name)
        try:
            rendered = Path(_render(template, date(2026, 1, 5)))
        except (KeyError, IndexError, ValueError) as err:
            raise ConfigError(f"{path}: {name} = {template!r} uses an unknown field {err}; "
                              f"available: {TEMPLATE_FIELDS}") from None
        if rendered.is_absolute() or ".." in rendered.parts:
            raise ConfigError(f"{path}: {name} must be a relative path inside journal_dir")
    try:
        cfg.notable()
    except re.error as err:
        raise ConfigError(f"{path}: notable_commands has an invalid pattern: {err}") from None
    return cfg


def _render(template: str, day: date) -> str:
    iso = day.isocalendar()
    week_start = day - timedelta(days=day.weekday())
    return template.format(
        date=day.isoformat(), year=f"{day.year:04d}", month=f"{day.month:02d}", day=f"{day.day:02d}",
        week=f"{iso.week:02d}", iso_year=f"{iso.year:04d}",
        week_start=week_start.isoformat(), week_end=(week_start + timedelta(days=6)).isoformat(),
    )


def note_path(cfg: Config, day: date) -> Path:
    return cfg.journal_dir / _render(cfg.day_note, day)


def paths(cfg: Config, day: date) -> dict:
    """Day and week note paths. Week fields come from the week's Monday, so every day maps to one file."""
    week_start = day - timedelta(days=day.weekday())
    return {
        "day_note": str(note_path(cfg, day)),
        "week_note": str(cfg.journal_dir / _render(cfg.week_note, week_start)),
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
    }


def read_note_ids(path: Path) -> tuple[set[str], set[str]]:
    """The `sessions` and `skipped` lists from a day note's frontmatter."""
    lists: dict[str, set[str]] = {"sessions": set(), "skipped": set()}
    if not path.is_file():
        return lists["sessions"], lists["skipped"]
    lines = path.read_text().splitlines()
    if not lines or lines[0].strip() != "---":
        return lists["sessions"], lists["skipped"]
    current = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if key:
            current = key.group(1) if key.group(1) in lists else None
            inline = key.group(2).strip()
            if current and inline.startswith("["):
                items = inline.strip("[]").split(",")
                lists[current].update(i.strip().strip("'\"") for i in items if i.strip())
            continue
        item = re.match(r"^\s*-\s*(.+?)\s*(#.*)?$", line)
        if current and item:
            lists[current].add(item.group(1).strip("'\""))
    return lists["sessions"], lists["skipped"]


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _excluded(cwd: str | None, cfg: Config) -> bool:
    if not cwd:
        return False
    for prefix in ALWAYS_EXCLUDED + [tempfile.gettempdir()] + cfg.exclude_cwds:
        if cwd == prefix or cwd.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


_repo_cache: dict[str, str | None] = {}


def _repo(cwd: str | None) -> str | None:
    """`owner/name` from the checkout's origin remote, when the directory still exists."""
    if not cwd:
        return None
    if cwd not in _repo_cache:
        repo = None
        if os.path.isdir(cwd):
            try:
                url = subprocess.run(
                    ["git", "-C", cwd, "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                match = re.search(r"[:/]([^/:]+/[^/]+?)(\.git)?$", url)
                repo = match.group(1) if match else None
            except (OSError, subprocess.SubprocessError):
                repo = None
        _repo_cache[cwd] = repo
    return _repo_cache[cwd]


class Digest:
    """What a session did inside one day's window."""

    def __init__(self, harness: str, sid: str, path: Path, funes_id: str, notable: re.Pattern | None = None):
        self.harness, self.sid, self.path, self.funes_id = harness, sid, path, funes_id
        self.notable = notable or re.compile("|".join(f"(?:{p})" for p in NOTABLE_COMMANDS))
        self.cwd: str | None = None
        self.branch: str | None = None
        self.title: str | None = None
        self.headless = False
        self.prompts: list[str] = []
        self.user_turns = 0
        self.tool_calls = 0
        self.files: list[str] = []
        self.commands: list[str] = []
        self.last_reply: str | None = None
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        self.active = False

    def touch(self, ts: str):
        self.active = True
        self.first_ts = self.first_ts or ts
        self.last_ts = ts

    def prompt(self, text: str):
        self.user_turns += 1
        if len(self.prompts) < MAX_PROMPTS:
            self.prompts.append(_clip(text, PROMPT_CHARS))

    def tool(self, name: str, args: dict):
        self.tool_calls += 1
        if name in EDIT_TOOLS:
            target = args.get("file_path") or args.get("path") or args.get("notebook_path")
            if isinstance(target, str) and target not in self.files and len(self.files) < MAX_FILES:
                self.files.append(target)
        elif name in SHELL_TOOLS:
            command = args.get("command")
            if isinstance(command, str) and self.notable.search(command) and len(self.commands) < MAX_COMMANDS:
                self.commands.append(_clip(command, COMMAND_CHARS))

    def as_candidate(self) -> dict:
        return {
            "key": f"{self.harness}:{self.sid}",
            "harness": self.harness,
            "id": self.sid,
            "funes_id": self.funes_id,
            "title": self.title,
            "cwd": self.cwd,
            "repo": _repo(self.cwd),
            "branch": self.branch,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "user_turns": self.user_turns,
            "prompts": self.prompts,
            "tool_calls": self.tool_calls,
            "files_edited": self.files,
            "notable_commands": self.commands,
            "last_reply": self.last_reply,
            "transcript": str(self.path),
        }


def _records(path: Path):
    try:
        with path.open() as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _claude_prompt_text(record: dict) -> str | None:
    """The text a person typed, or None for tool results and harness-written records."""
    if record.get("isMeta") or record.get("isSidechain"):
        return None
    content = record.get("message", {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str) or not content.strip():
        return None
    text = content.strip()
    command = COMMAND_NAME.search(text)
    if command:
        name = command.group(1).strip()
        if name in HOUSEKEEPING_COMMANDS:
            return None
        args = COMMAND_ARGS.search(text)
        return f"/{name} {args.group(1).strip()}".strip() if args else f"/{name}"
    if text.startswith(HARNESS_TEXT) or text.startswith("<command-message>"):
        return None
    return text


def local_zone() -> tzinfo:
    """The local IANA zone, so each past day gets its own DST offset.

    Falls back to today's fixed offset only when the zone can't be named.
    """
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        link = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in link:
            name = link.split("/zoneinfo/", 1)[1]
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    return datetime.now().astimezone().tzinfo


def _window(day: date, tz: tzinfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return start, start + timedelta(days=1)


def _in_window(ts: datetime | None, window) -> bool:
    return ts is not None and window[0] <= ts < window[1]


def digest_claude(path: Path, window, notable: re.Pattern | None = None) -> Digest:
    d = Digest("claude", path.stem, path, path.stem, notable)
    for record in _records(path):
        kind = record.get("type")
        if kind == "ai-title" and record.get("aiTitle"):
            d.title = record["aiTitle"]
        elif kind == "custom-title" and record.get("customTitle"):
            d.title = record["customTitle"]
        if kind not in ("user", "assistant"):
            continue
        d.cwd = d.cwd or record.get("cwd")
        if record.get("isSidechain"):
            continue
        ts = _parse_ts(record.get("timestamp"))
        if not _in_window(ts, window):
            continue
        d.touch(record["timestamp"])
        d.branch = record.get("gitBranch") or d.branch
        if str(record.get("entrypoint", "")).startswith("sdk"):
            d.headless = True
        if kind == "user":
            text = _claude_prompt_text(record)
            if text:
                d.prompt(text)
            continue
        for block in record.get("message", {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                d.tool(block.get("name", ""), block.get("input") or {})
            elif block.get("type") == "text" and block.get("text", "").strip():
                d.last_reply = _clip(block["text"], REPLY_CHARS)
    return d


def digest_omp(path: Path, window, notable: re.Pattern | None = None) -> Digest:
    stem = path.stem
    d = Digest("omp", stem.split("_", 1)[-1], path, stem, notable)
    for record in _records(path):
        kind = record.get("type")
        if kind == "session":
            d.sid = record.get("id") or d.sid
            d.cwd = record.get("cwd")
            d.title = d.title or record.get("title")
        elif kind == "title" and record.get("title"):
            d.title = record["title"]
        if kind != "message":
            continue
        ts = _parse_ts(record.get("timestamp"))
        if not _in_window(ts, window):
            continue
        d.touch(record["timestamp"])
        message = record.get("message") or {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        if message.get("role") == "user":
            text = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text").strip()
            if text and not text.startswith(HARNESS_TEXT):
                d.prompt(text)
        elif message.get("role") == "assistant":
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "toolCall":
                    args = block.get("arguments")
                    d.tool(block.get("name", ""), args if isinstance(args, dict) else {})
                elif block.get("type") == "text" and block.get("text", "").strip():
                    d.last_reply = _clip(block["text"], REPLY_CHARS)
    return d


def _top_level(root: Path) -> list[Path]:
    """Session transcripts one level below the root; deeper files are subagents."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*/*.jsonl") if p.is_file())


def scan(day: date, cfg: Config, tz: tzinfo | None = None) -> dict:
    tz = tz or local_zone()
    window = _window(day, tz)
    since = window[0].timestamp()
    journaled, skipped = read_note_ids(note_path(cfg, day))
    notable = cfg.notable()
    candidates, filtered = [], []
    sources = [(p, digest_claude) for p in _top_level(cfg.claude_projects)]
    sources += [(p, digest_omp) for p in _top_level(cfg.omp_sessions)]
    for path, reader in sources:
        try:
            if path.stat().st_mtime < since:
                continue  # untouched since the day began
        except OSError:
            continue
        d = reader(path, window, notable)
        if not d.active:
            continue
        key = f"{d.harness}:{d.sid}"
        reason = None
        if key in journaled:
            reason = "already journaled"
        elif key in skipped:
            reason = "already skipped"
        elif _excluded(d.cwd, cfg):
            reason = "excluded directory"
        elif d.headless and not cfg.include_headless:
            reason = "headless run"
        elif not d.user_turns:
            reason = "no prompts typed on this day"
        if reason:
            filtered.append({"key": key, "reason": reason, "cwd": d.cwd, "title": d.title})
        else:
            candidates.append(d.as_candidate())
    candidates.sort(key=lambda c: c["first_ts"] or "")
    return {
        "date": day.isoformat(),
        "note": str(note_path(cfg, day)),
        "candidates": candidates,
        "filtered": filtered,
    }


def _session_key(path: Path, harness: str) -> dict:
    if harness == "claude":
        return {"key": f"claude:{path.stem}", "harness": "claude", "id": path.stem,
                "funes_id": path.stem, "transcript": str(path)}
    sid = path.stem.split("_", 1)[-1]
    for record in _records(path):
        if record.get("type") == "session":
            sid = record.get("id") or sid
            break
    return {"key": f"omp:{sid}", "harness": "omp", "id": sid, "funes_id": path.stem, "transcript": str(path)}


def whoami(cfg: Config, env=None, nonce: str | None = None, wait_seconds: float = 5) -> dict | None:
    env = os.environ if env is None else env
    sid = env.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        matches = list(cfg.claude_projects.glob(f"*/{sid}.jsonl")) if cfg.claude_projects.is_dir() else []
        found = {"key": f"claude:{sid}", "harness": "claude", "id": sid, "funes_id": sid, "transcript": None}
        if matches:
            found["transcript"] = str(matches[0])
        return found
    if not nonce:
        return None
    deadline = time.monotonic() + wait_seconds
    while True:
        # Newest first: the current session was written to moments ago.
        recent = []
        for root, harness in ((cfg.omp_sessions, "omp"), (cfg.claude_projects, "claude")):
            for path in _top_level(root):
                try:
                    recent.append((path.stat().st_mtime, path, harness))
                except OSError:
                    continue
        for _, path, harness in sorted(recent, reverse=True)[:200]:
            try:
                if nonce in path.read_text(errors="ignore"):
                    return _session_key(path, harness)
            except OSError:
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cand = sub.add_parser("candidates", help="sessions active on a day, with digests")
    cand.add_argument("--date", help="YYYY-MM-DD, default today")
    cand.add_argument("--tz", help="IANA zone for day boundaries, default local")
    cand.add_argument("--config", type=Path)
    pth = sub.add_parser("paths", help="day and week note paths for a day")
    pth.add_argument("--date", help="YYYY-MM-DD, default today")
    pth.add_argument("--config", type=Path)
    who = sub.add_parser("whoami", help="the current session's key")
    who.add_argument("--nonce", help="a fresh token printed earlier in this session")
    who.add_argument("--config", type=Path)
    sub.add_parser("config-path", help="print the config file location")
    args = parser.parse_args(argv)

    if args.command == "config-path":
        print(default_config_path())
        return 0
    try:
        cfg = load_config(args.config or default_config_path())
    except ConfigError as err:
        print(f"agent-journal: {err}", file=sys.stderr)
        return 2
    if args.command == "candidates":
        day = date.fromisoformat(args.date) if args.date else date.today()
        tz = ZoneInfo(args.tz) if args.tz else None
        print(json.dumps(scan(day, cfg, tz), indent=2))
        return 0
    if args.command == "paths":
        day = date.fromisoformat(args.date) if args.date else date.today()
        print(json.dumps(paths(cfg, day), indent=2))
        return 0
    found = whoami(cfg, nonce=args.nonce)
    if not found:
        print("agent-journal: session not found; print a fresh token in one command, then pass it "
              "with --nonce in the next", file=sys.stderr)
        return 1
    print(json.dumps(found, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
