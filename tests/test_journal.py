"""Tests for skills/agent-journal/scripts/journal.py, run with fixture transcripts only."""

import importlib.util
import json
import subprocess
import sys
from datetime import date, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/agent-journal/scripts/journal.py"
spec = importlib.util.spec_from_file_location("journal", SCRIPT)
journal = importlib.util.module_from_spec(spec)
sys.modules["journal"] = journal
spec.loader.exec_module(journal)

DAY = date(2026, 10, 15)
UTC = timezone.utc


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def claude_user(ts, text, cwd="/work/repo", entrypoint="cli", sidechain=False):
    return {
        "type": "user", "timestamp": ts, "cwd": cwd, "gitBranch": "main",
        "entrypoint": entrypoint, "isSidechain": sidechain,
        "message": {"role": "user", "content": text},
    }


def claude_tool_result(ts, cwd="/work/repo"):
    return {
        "type": "user", "timestamp": ts, "cwd": cwd, "entrypoint": "cli",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    }


def claude_assistant(ts, text=None, tools=(), cwd="/work/repo"):
    content = [{"type": "tool_use", "name": n, "input": i} for n, i in tools]
    if text:
        content.append({"type": "text", "text": text})
    return {"type": "assistant", "timestamp": ts, "cwd": cwd, "message": {"role": "assistant", "content": content}}


def omp_records(cwd, sid, records):
    return [{"type": "session", "id": sid, "timestamp": "2026-10-15T09:00:00Z", "cwd": cwd, "title": "Omp title"}] + records


def omp_msg(ts, role, content):
    return {"type": "message", "timestamp": ts, "message": {"role": role, "content": content}}


@pytest.fixture
def roots(tmp_path):
    claude = tmp_path / "claude"
    omp = tmp_path / "omp"
    notes = tmp_path / "notes"
    claude.mkdir()
    omp.mkdir()
    notes.mkdir()
    return tmp_path, claude, omp, notes


def config(roots, **overrides):
    tmp, claude, omp, notes = roots
    values = dict(
        journal_dir=notes, claude_projects=claude, omp_sessions=omp,
        exclude_cwds=[], include_headless=False,
    )
    values.update(overrides)
    return journal.Config(**values)


def by_key(result):
    return {c["key"]: c for c in result["candidates"]}


def filtered(result):
    return {f["key"]: f["reason"] for f in result["filtered"]}


def test_claude_session_digest_counts_only_the_days_activity(roots):
    _, claude, _, _ = roots
    write_jsonl(claude / "-work-repo/aaa.jsonl", [
        claude_user("2026-10-14T22:00:00Z", "yesterday's ask"),
        claude_user("2026-10-15T10:00:00Z", "fix the parser"),
        claude_assistant("2026-10-15T10:01:00Z", tools=[
            ("Edit", {"file_path": "/work/repo/parse.py"}),
            ("Bash", {"command": "git commit -m 'Fix parser'"}),
            ("Read", {"file_path": "/work/repo/README.md"}),
        ]),
        claude_tool_result("2026-10-15T10:01:05Z"),
        claude_user("2026-10-15T11:00:00Z", "now open a PR"),
        claude_assistant("2026-10-15T11:05:00Z", text="Opened the PR.", tools=[
            ("Bash", {"command": "gh pr create --fill"}),
        ]),
        {"type": "ai-title", "aiTitle": "Parser fix"},
        claude_user("2026-10-16T09:00:00Z", "tomorrow's ask"),
    ])
    result = journal.scan(DAY, config(roots), tz=UTC)
    c = by_key(result)["claude:aaa"]
    assert c["prompts"] == ["fix the parser", "now open a PR"]
    assert c["user_turns"] == 2
    assert c["title"] == "Parser fix"
    assert c["cwd"] == "/work/repo"
    assert c["files_edited"] == ["/work/repo/parse.py"]
    assert c["notable_commands"] == ["git commit -m 'Fix parser'", "gh pr create --fill"]
    assert c["tool_calls"] == 4
    assert c["last_reply"] == "Opened the PR."
    assert c["first_ts"] == "2026-10-15T10:00:00Z"
    assert c["last_ts"] == "2026-10-15T11:05:00Z"
    assert c["funes_id"] == "aaa"
    assert c["transcript"].endswith("aaa.jsonl")


def test_session_started_on_an_earlier_day_is_listed(roots):
    _, claude, _, _ = roots
    write_jsonl(claude / "-work-repo/old.jsonl", [
        claude_user("2026-10-13T10:00:00Z", "start"),
        claude_user("2026-10-15T10:00:00Z", "continue today"),
    ])
    assert by_key(journal.scan(DAY, config(roots), tz=UTC))["claude:old"]["prompts"] == ["continue today"]


def test_sessions_idle_on_the_day_are_absent(roots):
    _, claude, _, _ = roots
    write_jsonl(claude / "-work-repo/idle.jsonl", [claude_user("2026-10-14T10:00:00Z", "only yesterday")])
    result = journal.scan(DAY, config(roots), tz=UTC)
    assert "claude:idle" not in by_key(result) and "claude:idle" not in filtered(result)


def test_day_boundaries_use_the_given_timezone(roots):
    _, claude, _, _ = roots
    # 02:00 UTC on 10/16 is still 10/15 in US Eastern.
    write_jsonl(claude / "-work-repo/late.jsonl", [claude_user("2026-10-16T02:00:00Z", "late night")])
    from zoneinfo import ZoneInfo
    assert "claude:late" in by_key(journal.scan(DAY, config(roots), tz=ZoneInfo("America/New_York")))
    assert "claude:late" not in by_key(journal.scan(DAY, config(roots), tz=UTC))


def test_subagent_transcripts_and_sidechain_records_are_ignored(roots):
    _, claude, _, _ = roots
    write_jsonl(claude / "-work-repo/main/subagents/agent-x.jsonl", [claude_user("2026-10-15T10:00:00Z", "sub")])
    write_jsonl(claude / "-work-repo/side.jsonl", [claude_user("2026-10-15T10:00:00Z", "side", sidechain=True)])
    result = journal.scan(DAY, config(roots), tz=UTC)
    assert not any("agent-x" in k for k in by_key(result) | filtered(result))
    assert "claude:side" not in by_key(result)


def test_headless_sessions_are_filtered_unless_included(roots):
    _, claude, _, _ = roots
    write_jsonl(claude / "-work-repo/bot.jsonl", [
        claude_user("2026-10-15T10:00:00Z", "headless", entrypoint="sdk-cli"),
    ])
    assert filtered(journal.scan(DAY, config(roots), tz=UTC))["claude:bot"] == "headless run"
    assert "claude:bot" in by_key(journal.scan(DAY, config(roots, include_headless=True), tz=UTC))


def test_excluded_cwds_are_filtered(roots):
    _, claude, _, _ = roots
    write_jsonl(claude / "-private/p.jsonl", [claude_user("2026-10-15T10:00:00Z", "hi", cwd="/data/me/personal/x")])
    write_jsonl(claude / "-tmp/t.jsonl", [claude_user("2026-10-15T10:00:00Z", "hi", cwd="/tmp/scratch")])
    write_jsonl(claude / "-near/n.jsonl", [claude_user("2026-10-15T10:00:00Z", "hi", cwd="/data/me/personality")])
    result = journal.scan(DAY, config(roots, exclude_cwds=["/data/me/personal"]), tz=UTC)
    assert filtered(result)["claude:p"] == "excluded directory"
    assert filtered(result)["claude:t"] == "excluded directory"  # temp dirs are always excluded
    assert "claude:n" in by_key(result)  # a prefix match needs a path boundary


def test_command_messages_and_meta_records_are_not_prompts(roots):
    _, claude, _, _ = roots
    meta = claude_user("2026-10-15T10:00:00Z", "<command-name>/clear</command-name>")
    caveat = claude_user("2026-10-15T10:00:01Z", "Caveat: generated by local commands")
    caveat["isMeta"] = True
    write_jsonl(claude / "-work-repo/cmd.jsonl", [meta, caveat])
    assert filtered(journal.scan(DAY, config(roots), tz=UTC))["claude:cmd"] == "no prompts typed on this day"


def test_long_prompts_and_replies_are_truncated(roots):
    _, claude, _, _ = roots
    write_jsonl(claude / "-work-repo/long.jsonl", [
        claude_user("2026-10-15T10:00:00Z", "x" * 5000),
        claude_assistant("2026-10-15T10:01:00Z", text="y" * 5000),
    ])
    c = by_key(journal.scan(DAY, config(roots), tz=UTC))["claude:long"]
    assert len(c["prompts"][0]) <= journal.PROMPT_CHARS + 1
    assert len(c["last_reply"]) <= journal.REPLY_CHARS + 1


def test_omp_session_is_parsed(roots):
    _, _, omp, _ = roots
    sid = "01a1-omp"
    write_jsonl(omp / "-work-repo" / f"2026-10-15T09-00-00-000Z_{sid}.jsonl", omp_records("/work/repo", sid, [
        omp_msg("2026-10-15T09:00:01Z", "user", [{"type": "text", "text": "refactor the loader"}]),
        omp_msg("2026-10-15T09:00:02Z", "assistant", [
            {"type": "toolCall", "name": "edit", "arguments": {"path": "/work/repo/loader.py"}},
            {"type": "toolCall", "name": "bash", "arguments": {"command": "git push origin main"}},
        ]),
        omp_msg("2026-10-15T09:00:03Z", "toolResult", [{"type": "text", "text": "done"}]),
        omp_msg("2026-10-15T09:00:04Z", "assistant", [{"type": "text", "text": "Refactored."}]),
    ]))
    # A subagent session in the per-session directory is not a top-level session.
    write_jsonl(omp / "-work-repo" / f"2026-10-15T09-00-00-000Z_{sid}" / "sub.jsonl",
                omp_records("/work/repo", "sub", [omp_msg("2026-10-15T09:00:01Z", "user", [{"type": "text", "text": "x"}])]))
    result = journal.scan(DAY, config(roots), tz=UTC)
    c = by_key(result)[f"omp:{sid}"]
    assert c["prompts"] == ["refactor the loader"]
    assert c["title"] == "Omp title"
    assert c["files_edited"] == ["/work/repo/loader.py"]
    assert c["notable_commands"] == ["git push origin main"]
    assert c["last_reply"] == "Refactored."
    assert c["funes_id"] == f"2026-10-15T09-00-00-000Z_{sid}"
    assert "omp:sub" not in by_key(result)


def test_sessions_already_in_the_days_note_are_filtered(roots):
    _, claude, _, notes = roots
    write_jsonl(claude / "-work-repo/done.jsonl", [claude_user("2026-10-15T10:00:00Z", "a")])
    write_jsonl(claude / "-work-repo/skip.jsonl", [claude_user("2026-10-15T10:00:00Z", "b")])
    write_jsonl(claude / "-work-repo/new.jsonl", [claude_user("2026-10-15T10:00:00Z", "c")])
    (notes / "2026-10-15-journal.md").write_text(
        "---\ndate: 2026-10-15\nsessions:\n  - claude:done\nskipped:\n  - claude:skip\ntags: [agent-journal]\n---\n\n# Journal\n"
    )
    result = journal.scan(DAY, config(roots), tz=UTC)
    assert filtered(result)["claude:done"] == "already journaled"
    assert filtered(result)["claude:skip"] == "already skipped"
    assert "claude:new" in by_key(result)


def test_read_note_ids_handles_missing_note_and_inline_lists(tmp_path):
    assert journal.read_note_ids(tmp_path / "missing.md") == (set(), set())
    note = tmp_path / "n.md"
    note.write_text("---\nsessions: [claude:a, omp:b]\nskipped: []\n---\n")
    assert journal.read_note_ids(note) == ({"claude:a", "omp:b"}, set())


def test_load_config_expands_home_and_applies_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "config.toml"
    path.write_text('journal_dir = "~/journal"\nexclude_cwds = ["~/personal"]\n')
    cfg = journal.load_config(path)
    assert cfg.journal_dir == tmp_path / "journal"
    assert cfg.exclude_cwds == [str(tmp_path / "personal")]
    assert cfg.claude_projects == tmp_path / ".claude/projects"
    assert cfg.omp_sessions == tmp_path / ".omp/agent/sessions"
    assert cfg.include_headless is False


def test_load_config_requires_journal_dir(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("include_headless = true\n")
    with pytest.raises(journal.ConfigError, match="journal_dir"):
        journal.load_config(path)


def test_whoami_uses_the_claude_session_variable(roots):
    assert journal.whoami(config(roots), env={"CLAUDE_CODE_SESSION_ID": "abc"}, nonce=None)["key"] == "claude:abc"


def test_whoami_finds_an_omp_session_by_nonce(roots):
    _, _, omp, _ = roots
    write_jsonl(omp / "-work-repo/2026-10-15T09-00-00-000Z_s1.jsonl", omp_records("/work/repo", "s1", [
        omp_msg("2026-10-15T09:00:02Z", "assistant", [
            {"type": "toolCall", "name": "bash", "arguments": {"command": "journal.py whoami --nonce aj-123"}},
        ]),
    ]))
    write_jsonl(omp / "-work-repo/2026-10-15T08-00-00-000Z_s2.jsonl", omp_records("/work/repo", "s2", []))
    found = journal.whoami(config(roots), env={}, nonce="aj-123", wait_seconds=0)
    assert found["key"] == "omp:s1"
    assert journal.whoami(config(roots), env={}, nonce="aj-absent", wait_seconds=0) is None


def test_cli_candidates_prints_json(roots, tmp_path):
    _, claude, omp, notes = roots
    write_jsonl(claude / "-work-repo/cli.jsonl", [claude_user("2026-10-15T10:00:00Z", "hello")])
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'journal_dir = "{notes}"\nclaude_projects = "{claude}"\nomp_sessions = "{omp}"\n'
    )
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "candidates", "--date", "2026-10-15", "--config", str(cfg), "--tz", "UTC"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    assert data["date"] == "2026-10-15"
    assert data["note"] == str(notes / "2026-10-15-journal.md")
    assert [c["key"] for c in data["candidates"]] == ["claude:cli"]


def test_note_paths_follow_the_configured_templates(roots):
    _, _, _, notes = roots
    cfg = config(roots, day_note="{year}/{month}/{date}.md", week_note="{year}/weeks/{year}-W{week}.md")
    assert journal.note_path(cfg, DAY) == notes / "2026/10/2026-10-15.md"
    paths = journal.paths(cfg, DAY)
    assert paths["day_note"] == str(notes / "2026/10/2026-10-15.md")
    assert paths["week_note"] == str(notes / "2026/weeks/2026-W42.md")
    assert (paths["week_start"], paths["week_end"]) == ("2026-10-12", "2026-10-18")


def test_default_note_paths(roots):
    _, _, _, notes = roots
    paths = journal.paths(config(roots), DAY)
    assert paths["day_note"] == str(notes / "2026-10-15-journal.md")
    assert paths["week_note"] == str(notes / "2026-10-12-week.md")


def test_scan_reads_the_templated_day_note(roots):
    _, claude, _, notes = roots
    write_jsonl(claude / "-work-repo/done.jsonl", [claude_user("2026-10-15T10:00:00Z", "a")])
    (notes / "2026").mkdir()
    (notes / "2026/2026-10-15.md").write_text("---\nsessions:\n  - claude:done\n---\n")
    result = journal.scan(DAY, config(roots, day_note="{year}/{date}.md"), tz=UTC)
    assert filtered(result)["claude:done"] == "already journaled"
    assert result["note"] == str(notes / "2026/2026-10-15.md")


def test_unknown_template_field_is_a_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('journal_dir = "/j"\nday_note = "{when}.md"\n')
    with pytest.raises(journal.ConfigError, match="day_note"):
        journal.load_config(path)


def test_templates_must_stay_inside_the_journal_dir(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('journal_dir = "/j"\nweek_note = "../{week_start}.md"\n')
    with pytest.raises(journal.ConfigError, match="week_note"):
        journal.load_config(path)


def test_configured_notable_commands_extend_the_defaults(roots):
    _, claude, _, _ = roots
    write_jsonl(claude / "-work-repo/cmd.jsonl", [
        claude_user("2026-10-15T10:00:00Z", "file it"),
        claude_assistant("2026-10-15T10:01:00Z", tools=[
            ("Bash", {"command": "tracker create --title x"}),
            ("Bash", {"command": "git push"}),
            ("Bash", {"command": "ls"}),
        ]),
    ])
    plain = by_key(journal.scan(DAY, config(roots), tz=UTC))["claude:cmd"]
    assert plain["notable_commands"] == ["git push"]
    extended = by_key(journal.scan(DAY, config(roots, notable_commands=[r"\btracker\s+create\b"]), tz=UTC))["claude:cmd"]
    assert extended["notable_commands"] == ["tracker create --title x", "git push"]


def test_bad_notable_command_pattern_is_a_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('journal_dir = "/j"\nnotable_commands = ["("]\n')
    with pytest.raises(journal.ConfigError, match="notable_commands"):
        journal.load_config(path)


def test_cli_paths_prints_json(roots, tmp_path):
    _, claude, omp, notes = roots
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'journal_dir = "{notes}"\nclaude_projects = "{claude}"\nomp_sessions = "{omp}"\n')
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "paths", "--date", "2026-10-15", "--config", str(cfg)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(out.stdout)["week_note"] == str(notes / "2026-10-12-week.md")


def test_the_systems_temp_directory_is_always_excluded(roots, monkeypatch):
    _, claude, _, _ = roots
    monkeypatch.setattr(journal.tempfile, "gettempdir", lambda: "/scratch/tmp")
    write_jsonl(claude / "-scratch/s.jsonl", [claude_user("2026-10-15T10:00:00Z", "hi", cwd="/scratch/tmp/run1")])
    assert filtered(journal.scan(DAY, config(roots), tz=UTC))["claude:s"] == "excluded directory"
