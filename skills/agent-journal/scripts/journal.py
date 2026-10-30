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
  journal.py prepare --from YYYY-MM-DD [--to YYYY-MM-DD] --out DIR [--tz NAME] [--config PATH]
      For a Claude Code Workflow catch-up: save each day's candidates to DIR, copy the workflow
      script there, write the Workflow args to DIR/args.json, and print a summary.
  journal.py merge RESULT [--heading OLD=NEW ...] [--write] [--config PATH]
      Write a Workflow catch-up result (or the tool's output file holding it) into the day notes.
      Without --write, report what would change. Each day's keys are checked against its saved
      candidates first; unfinished sessions stay out of the note so the next catch-up lists them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
    scope: str = ""

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
        scope=str(data.get("scope", "")),
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
    if not path.is_file():
        return set(), set()
    front, _ = _split_note(path.read_text())
    _, lists, _ = _pull_lists(front)
    return set(lists["sessions"]), set(lists["skipped"])


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


WORKFLOW_SCRIPT = Path(__file__).resolve().parent / "catch-up.workflow.js"
SKILL_DIR = Path(__file__).resolve().parents[1]
NEW_NOTE = "---\ndate: {date}\ntags: [agent-journal]\n---\n\n# Journal: {date}\n"


def prepare(cfg: Config, days: list[date], out_dir: Path, tz: tzinfo | None = None) -> dict:
    """Save each day's candidates and build the Workflow tool's args, so the digests stay in files."""
    out_dir = Path(out_dir).resolve()  # the agents and merge read these paths from other directories
    out_dir.mkdir(parents=True, exist_ok=True)
    counts, arg_days = {}, []
    for day in days:
        result = scan(day, cfg, tz)
        saved = out_dir / f"candidates-{day.isoformat()}.json"
        saved.write_text(json.dumps(result, indent=2))
        keys = [c["key"] for c in result["candidates"]]
        counts[day.isoformat()] = len(keys)
        if keys:
            arg_days.append({"date": day.isoformat(), "candidates_file": str(saved), "note": result["note"], "keys": keys})
    # The Workflow tool loads a script only from a directory the session can read, such as its scratch directory.
    script = out_dir / WORKFLOW_SCRIPT.name
    shutil.copyfile(WORKFLOW_SCRIPT, script)
    args_file = out_dir / "args.json"
    args_file.write_text(json.dumps({"skill_dir": str(SKILL_DIR), "scope": cfg.scope.strip(), "days": arg_days}))
    return {"script_path": str(script), "args_file": str(args_file), "total": sum(counts.values()), "counts": counts}


def _split_note(text: str) -> tuple[list[str], str]:
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[1:i], "\n".join(lines[i + 1:])
    return [], text


def _pull_lists(front: list[str]) -> tuple[list[str], dict[str, list[str]], int]:
    """Frontmatter without its sessions/skipped lists, the lists, and where to put them back."""
    kept, lists, at, current = [], {"sessions": [], "skipped": []}, None, None
    for line in front:
        if line[:1] not in ("", " ", "\t", "-"):  # any top-level line ends the list before it
            key = re.match(r"^([^:]+):\s*(.*)$", line)
            current = key.group(1).strip() if key and key.group(1).strip() in lists else None
            if current:
                at = len(kept) if at is None else at
                inline = key.group(2).strip()
                if inline.startswith("["):
                    lists[current] += [i.strip().strip("'\"") for i in inline.strip("[]").split(",") if i.strip()]
                continue
            kept.append(line)
            continue
        item = re.match(r"^\s*-\s*(.+?)\s*(#.*)?$", line)
        if current and item:
            lists[current].append(item.group(1).strip("'\""))
            continue
        kept.append(line)
    if at is None:
        at = next((i + 1 for i, line in enumerate(kept) if line.startswith("date:")), 0)
    return kept, lists, at


FENCE = re.compile(r"^\s*(```|~~~)")


def _headings(text: str, prefix: str) -> list[tuple[int, str]]:
    """Offset and line of each heading starting with prefix, skipping fenced code blocks."""
    found, pos, fence = [], 0, None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if m:
            fence = m.group(1) if fence is None else (None if m.group(1) == fence else fence)
        elif fence is None and line.startswith(prefix):
            found.append((pos, line))
        pos += len(line) + 1
    return found


def _sections(body: str) -> tuple[str, list[list[str]]]:
    starts = [p for p, _ in _headings(body, "## ")]
    if not starts:
        return body, []
    secs = []
    for i, p in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        head, _, text = body[p:end].partition("\n")
        secs.append([head[3:].strip(), text])
    return body[:starts[0]], secs


def _place(secs: list[list[str]], heading: str, markdown: str, extends: str) -> str:
    """Put one entry in the note's sections: replace the entry it extends, or add it under its heading."""
    markdown = markdown.strip() + "\n"
    if extends:
        target = extends.strip()
        matches = {id(s): [p for p, line in _headings(s[1], "### ") if line.rstrip() == target] for s in secs}
        # The entry's own section first; elsewhere only when exactly one entry carries that heading.
        own = [s for s in secs if s[0] == heading and matches[id(s)]]
        pool = own or [s for s in secs if matches[id(s)]]
        if sum(len(matches[id(s)]) for s in pool) == 1:
            sec = pool[0]
            start = matches[id(sec)][0]
            later = [p for p, _ in _headings(sec[1], "### ") if p > start]
            end = later[0] if later else len(sec[1])
            sec[1] = sec[1][:start] + markdown + "\n" + sec[1][end:].lstrip("\n")
            return "extended"
    for sec in secs:
        if sec[0] == heading:
            sec[1] = sec[1].rstrip("\n") + "\n\n" + markdown
            return "added"
    at = next((i for i, sec in enumerate(secs) if sec[0] == "Skipped"), len(secs))
    secs.insert(at, [heading, "\n" + markdown])
    return "new heading"


def _merge_day(day: dict, headings: dict[str, str], write: bool) -> dict:
    report = {"date": day["date"], "note": day["note"], "added": 0, "extended": 0, "new_headings": [],
              "sessions_added": 0, "skipped_added": 0, "rerun": [], "warnings": []}
    cands = json.loads(Path(day["candidates_file"]).read_text())["candidates"]
    info = {c["key"]: c for c in cands}
    decisions = {d["key"]: d for d in day.get("decisions", [])}
    judged = set(decisions) | set(day.get("unjudged", []))
    if judged != set(info):
        missing, unknown = sorted(set(info) - judged), sorted(judged - set(info))
        report["error"] = (f"keys don't match {day['candidates_file']}: missing {missing}, unknown {unknown}; "
                           "a key was likely miscopied into the Workflow args. Nothing written for this day.")
        return report

    note = Path(day["note"])
    text = note.read_text() if note.is_file() else NEW_NOTE.format(date=day["date"])
    front, body = _split_note(text)
    kept, lists, at = _pull_lists(front)
    pre, secs = _sections(body)
    problems = " ".join(day.get("problems", []))
    rerun = set(day.get("unjudged", []))
    skip_lines, out_of_scope = [], 0

    def add(keys, project, markdown, extends=""):
        present = [k for k in keys if k in lists["sessions"]]
        if len(present) == len(keys):
            report["warnings"].append(f"already in the note, not added again: {', '.join(keys)}")
            return
        if present and not extends:
            report["warnings"].append(f"{', '.join(present)} already in the note; check it doesn't now hold this work twice")
        if not markdown.lstrip().startswith("### "):
            report["warnings"].append(f"entry for {', '.join(keys)} doesn't start with a '### ' heading")
        heading = headings.get(project, project)
        how = _place(secs, heading, markdown, extends)
        if how == "new heading":
            report["new_headings"].append(heading)
        report["extended" if how == "extended" else "added"] += 1
        if extends and how != "extended":
            report["warnings"].append(f"no single entry headed {extends!r} to extend; added it as a new entry")
        for k in keys:
            if k not in lists["sessions"]:
                lists["sessions"].append(k)
                report["sessions_added"] += 1

    drafted = set()
    for e in day.get("entries", []):
        add(e["keys"], e["project"], e["markdown"], e.get("extends", ""))
        drafted.update(e["keys"])
    lessons = {l["key"]: l for l in day.get("lessons", [])}
    for key, d in decisions.items():
        title = f'"{info[key].get("title") or "untitled"}" ({info[key].get("harness", "?")})'
        if d["decision"] == "entry" and key not in drafted:
            rerun.add(key)
        elif d["decision"] == "lesson":
            lesson = lessons.get(key)
            if lesson and lesson["markdown"].strip():
                add([key], lesson["project"], lesson["markdown"])
            elif lesson is None or key in problems:
                rerun.add(key)
            elif key not in lists["skipped"]:
                skip_lines.append(f"- {title}: no lasting lesson")
                lists["skipped"].append(key)
        elif d["decision"] in ("skip", "out of scope") and key not in lists["skipped"]:
            lists["skipped"].append(key)
            if d["decision"] == "skip":
                skip_lines.append(f"- {title}: {d['reason'].rstrip('.')}")
            else:
                out_of_scope += 1
    report["skipped_added"] = len(skip_lines) + out_of_scope
    if skip_lines or out_of_scope:
        sec = next((s for s in secs if s[0] == "Skipped"), None)
        if sec is None:
            sec = ["Skipped", "\n"]
            secs.append(sec)
        if out_of_scope:  # one running count, so out-of-scope sessions never leave detail
            counted = re.compile(r"(?m)^- (\d+) out-of-scope sessions?\n?")
            earlier = counted.search(sec[1])
            if earlier:
                out_of_scope += int(earlier.group(1))
                sec[1] = counted.sub("", sec[1], count=1)
            skip_lines.append(f"- {out_of_scope} out-of-scope session{'s' if out_of_scope > 1 else ''}")
        sec[1] = sec[1].rstrip("\n") + "\n" + "\n".join(skip_lines) + "\n"
    report["rerun"] = sorted(rerun)

    listed = [f"{name}:\n" + "".join(f"  - {k}\n" for k in keys) for name, keys in lists.items() if keys]
    front_text = "\n".join(kept[:at]) + ("\n" if at else "") + "".join(listed) + "\n".join(kept[at:])
    if secs and pre.strip():
        pre = pre.rstrip("\n") + "\n\n"
    body_text = pre + "".join(f"## {h}\n{t.rstrip(chr(10))}\n\n" for h, t in secs)
    if write:
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"---\n{front_text.rstrip(chr(10))}\n---\n{body_text.rstrip()}\n")
    return report


def merge(result: dict, headings: dict[str, str] | None = None, write: bool = False) -> dict:
    """Write a Workflow catch-up result into its day notes; without write, only report."""
    if "days" not in result and "result" in result:  # the Workflow tool's output file wraps the return value
        result = result["result"]
        result = json.loads(result) if isinstance(result, str) else result
    days = [_merge_day(day, headings or {}, write) for day in result["days"]]
    return {"wrote": write, "days": days}


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
    prep = sub.add_parser("prepare", help="save candidates and Workflow args for a Claude Code catch-up")
    prep.add_argument("--from", dest="start", required=True, help="first day, YYYY-MM-DD")
    prep.add_argument("--to", dest="end", help="last day, YYYY-MM-DD; default the first")
    prep.add_argument("--out", type=Path, required=True, help="directory for the files, such as your scratch directory")
    prep.add_argument("--tz", help="IANA zone for day boundaries, default local")
    prep.add_argument("--config", type=Path)
    mrg = sub.add_parser("merge", help="write a Workflow catch-up result into the day notes")
    mrg.add_argument("result", type=Path, help="the result JSON, or the Workflow tool's output file")
    mrg.add_argument("--heading", action="append", default=[], metavar="OLD=NEW",
                     help="put entries whose project is OLD under the NEW heading; repeatable")
    mrg.add_argument("--write", action="store_true", help="write the notes; without it, only report")
    mrg.add_argument("--config", type=Path)
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
    if args.command == "prepare":
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end) if args.end else start
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        tz = ZoneInfo(args.tz) if args.tz else None
        print(json.dumps(prepare(cfg, days, args.out, tz), indent=2))
        return 0
    if args.command == "merge":
        bad = [h for h in args.heading if "=" not in h]
        if bad:
            print(f"agent-journal: --heading takes OLD=NEW, got {bad}", file=sys.stderr)
            return 2
        headings = dict(h.split("=", 1) for h in args.heading)
        report = merge(json.loads(args.result.read_text()), headings, args.write)
        print(json.dumps(report, indent=2))
        return 1 if any("error" in d for d in report["days"]) else 0
    found = whoami(cfg, nonce=args.nonce)
    if not found:
        print("agent-journal: session not found; print a fresh token in one command, then pass it "
              "with --nonce in the next", file=sys.stderr)
        return 1
    print(json.dumps(found, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
