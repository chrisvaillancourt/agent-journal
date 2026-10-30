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


@pytest.fixture
def eastern_local_time(monkeypatch):
    import time

    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_default_timezone_applies_each_days_own_dst_offset(roots, eastern_local_time):
    _, claude, _, _ = roots
    # 04:30 UTC is 23:30 the day before in January (EST) but 00:30 the same day in July (EDT).
    # A fixed offset taken from today's clock gets one of the two wrong, whatever the season.
    write_jsonl(claude / "-work-repo/jan.jsonl", [claude_user("2026-01-15T04:30:00Z", "winter")])
    write_jsonl(claude / "-work-repo/jul.jsonl", [claude_user("2026-07-15T04:30:00Z", "summer")])
    assert "claude:jan" not in by_key(journal.scan(date(2026, 1, 15), config(roots)))
    assert "claude:jul" in by_key(journal.scan(date(2026, 7, 15), config(roots)))


def test_a_posix_tz_rule_that_names_no_zone_falls_back_to_the_local_offset(monkeypatch):
    import time

    monkeypatch.setenv("TZ", "EST5EDT,M3.2.0,M11.1.0")
    time.tzset()
    try:
        assert journal.local_zone().utcoffset(None) is not None
    finally:
        monkeypatch.undo()
        time.tzset()


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


# prepare and merge: the Workflow route's tools.

WORKFLOW = SCRIPT.parent / "catch-up.workflow.js"


def test_load_config_reads_scope(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('journal_dir = "/j"\nscope = """\nWork only.\n"""\n')
    assert journal.load_config(path).scope.strip() == "Work only."


def test_prepare_saves_each_day_and_builds_the_workflow_args(roots, tmp_path):
    _, claude, _, notes = roots
    write_jsonl(claude / "-work-repo/a.jsonl", [claude_user("2026-10-15T10:00:00Z", "first")])
    write_jsonl(claude / "-work-repo/b.jsonl", [claude_user("2026-10-15T11:00:00Z", "second")])
    out = tmp_path / "run"
    cfg = config(roots, scope="Work only.")
    summary = journal.prepare(cfg, [DAY, date(2026, 10, 16)], out, tz=UTC)

    assert summary["total"] == 2
    assert summary["counts"] == {"2026-10-15": 2, "2026-10-16": 0}
    assert Path(summary["script_path"]).read_text() == WORKFLOW.read_text()
    args = json.loads(Path(summary["args_file"]).read_text())
    assert args["skill_dir"] == str(SCRIPT.parents[1])
    assert args["scope"] == "Work only."
    assert [d["date"] for d in args["days"]] == ["2026-10-15"]  # a day with nothing to judge is left out
    day = args["days"][0]
    assert day["note"] == str(notes / "2026-10-15-journal.md")
    saved = json.loads(Path(day["candidates_file"]).read_text())
    assert day["keys"] == [c["key"] for c in saved["candidates"]] == ["claude:a", "claude:b"]
    assert (out / "candidates-2026-10-16.json").is_file()


def candidates_file(tmp_path, day, items):
    path = tmp_path / f"candidates-{day}.json"
    path.write_text(json.dumps({"date": day, "candidates": [
        {"key": k, "harness": k.split(":")[0], "title": t} for k, t in items]}))
    return path


def result_day(note, cands, **fields):
    keys = [c["key"] for c in json.loads(cands.read_text())["candidates"]]
    day = {"date": "2026-10-15", "note": str(note), "candidates_file": str(cands), "candidates": len(keys),
           "unjudged": [], "decisions": [], "entries": [], "lessons": [], "problems": []}
    day.update(fields)
    return day


def test_merge_writes_a_new_note_with_entries_lessons_and_skips(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    cands = candidates_file(tmp_path, "2026-10-15", [
        ("claude:e1", "Build"), ("claude:e2", "Build, resumed"), ("omp:l1", "Gotcha"),
        ("claude:s1", "Lookup"), ("claude:o1", "Hobby")])
    day = result_day(note, cands, decisions=[
        {"key": "claude:e1", "decision": "entry", "reason": "r"},
        {"key": "claude:e2", "decision": "entry", "reason": "r"},
        {"key": "omp:l1", "decision": "lesson", "reason": "r"},
        {"key": "claude:s1", "decision": "skip", "reason": "Quick lookup."},
        {"key": "claude:o1", "decision": "out of scope", "reason": "personal"}],
        entries=[{"project": "repo", "markdown": "### Built it #win\n\n- **Did:** x", "keys": ["claude:e1", "claude:e2"],
                  "extends": "", "win": True}],
        lessons=[{"key": "omp:l1", "project": "Tools", "markdown": "### A gotcha\n- **Learned:** y"}])
    report = journal.merge({"days": [day]}, write=True)

    text = note.read_text()
    sessions, skipped = journal.read_note_ids(note)
    assert sessions == {"claude:e1", "claude:e2", "omp:l1"}
    assert skipped == {"claude:s1", "claude:o1"}
    assert "# Journal: 2026-10-15" in text
    assert text.index("## repo") < text.index("### Built it #win") < text.index("## Tools") < text.index("## Skipped")
    assert '- "Lookup" (claude): Quick lookup' in text
    assert "1 out-of-scope session" in text and "Hobby" not in text
    assert report["days"][0]["rerun"] == []


def test_merge_extends_an_entry_reuses_headings_and_keeps_the_rest(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    note.write_text(
        "---\ndate: 2026-10-15\nsessions:\n  - claude:old\ntags:\n  - custom\n---\n\n# Journal: 2026-10-15\n\n"
        "## Agent tooling\n\n### First thing\n\n- **Did:** keep me\n\n### Second thing\n\n- **Did:** old text\n")
    cands = candidates_file(tmp_path, "2026-10-15", [("claude:n1", "More"), ("claude:n2", "New")])
    day = result_day(note, cands, decisions=[
        {"key": "claude:n1", "decision": "entry", "reason": "r"},
        {"key": "claude:n2", "decision": "entry", "reason": "r"}],
        entries=[
            {"project": "Agent tooling", "markdown": "### Second thing\n\n- **Did:** old text, plus more",
             "keys": ["claude:n1"], "extends": "### Second thing", "win": False},
            {"project": "agent-tooling", "markdown": "### Third thing\n\n- **Did:** z",
             "keys": ["claude:n2"], "extends": "", "win": False}])
    journal.merge({"days": [day]}, headings={"agent-tooling": "Agent tooling"}, write=True)

    text = note.read_text()
    assert journal.read_note_ids(note)[0] == {"claude:old", "claude:n1", "claude:n2"}
    assert "tags:\n  - custom" in text
    assert text.count("## Agent tooling") == 1
    assert "keep me" in text and "old text, plus more" in text and "- **Did:** old text\n" not in text
    assert text.index("### First thing") < text.index("### Second thing") < text.index("### Third thing")


def test_merge_leaves_unfinished_work_for_the_next_catch_up(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    cands = candidates_file(tmp_path, "2026-10-15", [
        ("claude:u", "Unjudged"), ("claude:d", "Draft failed"), ("claude:lm", "Lesson missing"),
        ("claude:lf", "Lesson failed"), ("claude:ln", "No lesson")])
    day = result_day(note, cands, unjudged=["claude:u"], decisions=[
        {"key": "claude:d", "decision": "entry", "reason": "r"},
        {"key": "claude:lm", "decision": "lesson", "reason": "r"},
        {"key": "claude:lf", "decision": "lesson", "reason": "r"},
        {"key": "claude:ln", "decision": "lesson", "reason": "r"}],
        lessons=[{"key": "claude:lf", "project": "P", "markdown": ""},
                 {"key": "claude:ln", "project": "P", "markdown": ""}],
        problems=["draft 2026-10-15 failed for claude:d", "lessons 2026-10-15 returned nothing for claude:lf"])
    report = journal.merge({"days": [day]}, write=True)

    sessions, skipped = journal.read_note_ids(note)
    assert sessions == set()
    assert skipped == {"claude:ln"}
    assert sorted(report["days"][0]["rerun"]) == ["claude:d", "claude:lf", "claude:lm", "claude:u"]


def test_merge_refuses_a_day_whose_keys_dont_match_its_candidates(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    cands = candidates_file(tmp_path, "2026-10-15", [("claude:real", "Real")])
    day = result_day(note, cands, decisions=[{"key": "claude:typo", "decision": "skip", "reason": "r"}])
    report = journal.merge({"days": [day]}, write=True)
    assert not note.exists()
    assert "claude:real" in report["days"][0]["error"]


def test_merge_without_write_changes_nothing(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    cands = candidates_file(tmp_path, "2026-10-15", [("claude:s", "Lookup")])
    day = result_day(note, cands, decisions=[{"key": "claude:s", "decision": "skip", "reason": "r"}])
    report = journal.merge({"days": [day]})
    assert not note.exists()
    assert report["days"][0]["skipped_added"] == 1 and report["wrote"] is False


def test_cli_merge_reads_a_workflow_output_file(roots, tmp_path):
    _, claude, omp, notes = roots
    note = notes / "2026-10-15-journal.md"
    cands = candidates_file(tmp_path, "2026-10-15", [("claude:s", "Lookup")])
    day = result_day(note, cands, decisions=[{"key": "claude:s", "decision": "skip", "reason": "r"}])
    output = tmp_path / "task.output"
    output.write_text(json.dumps({"summary": "s", "result": {"days": [day]}}))  # the Workflow tool's wrapper
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'journal_dir = "{notes}"\nclaude_projects = "{claude}"\nomp_sessions = "{omp}"\n')
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "merge", str(output), "--write", "--config", str(cfg)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(out.stdout)["wrote"] is True
    assert journal.read_note_ids(note)[1] == {"claude:s"}


def merge_one(tmp_path, note, cands_items, decisions, **fields):
    cands = candidates_file(tmp_path, "2026-10-15", cands_items)
    return journal.merge({"days": [result_day(note, cands, decisions=decisions, **fields)]}, write=True)


def test_merge_extends_the_entry_under_its_own_project(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    note.write_text("---\ndate: 2026-10-15\n---\n\n# J\n\n## alpha\n\n### Review fixes\n\n- alpha fact\n\n"
                    "## beta\n\n### Review fixes\n\n- beta fact\n")
    merge_one(tmp_path, note, [("claude:b", "B")], [{"key": "claude:b", "decision": "entry", "reason": "r"}],
              entries=[{"project": "beta", "markdown": "### Review fixes\n\n- beta fact, more", "keys": ["claude:b"],
                        "extends": "### Review fixes", "win": False}])
    text = note.read_text()
    assert "alpha fact" in text and "beta fact, more" in text and "- beta fact\n" not in text
    assert text.index("## alpha") < text.index("alpha fact") < text.index("## beta") < text.index("beta fact, more")


def test_merge_keeps_lists_under_frontmatter_keys_it_does_not_own(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    note.write_text("---\ndate: 2026-10-15\nsessions:\n  - claude:a\nrelated notes:\n  - x\ntags: [t]\n---\n\n# J\n")
    merge_one(tmp_path, note, [("claude:s", "S")], [{"key": "claude:s", "decision": "skip", "reason": "r"}])
    text = note.read_text()
    assert journal.read_note_ids(note) == ({"claude:a"}, {"claude:s"})
    assert "related notes:\n  - x\n" in text


def test_merge_starts_a_new_heading_on_its_own_line(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    note.write_text("---\ndate: 2026-10-15\n---\n\n# J\n\nIntro paragraph")
    merge_one(tmp_path, note, [("claude:e", "E")], [{"key": "claude:e", "decision": "entry", "reason": "r"}],
              entries=[{"project": "repo", "markdown": "### Did it\n\n- **Did:** x", "keys": ["claude:e"],
                        "extends": "", "win": False}])
    assert "Intro paragraph\n\n## repo\n" in note.read_text()


def test_merge_ignores_headings_inside_fenced_code(roots, tmp_path):
    _, _, _, notes = roots
    note = notes / "2026-10-15-journal.md"
    fenced = "```md\n## Skipped\n### not heading\n```"
    note.write_text(f"---\ndate: 2026-10-15\n---\n\n# J\n\n## repo\n\n### A\n\n- old\n\n{fenced}\n\n### Kept\n\n- kept\n")
    merge_one(tmp_path, note, [("claude:a", "A"), ("claude:s", "S")], [
        {"key": "claude:a", "decision": "entry", "reason": "r"},
        {"key": "claude:s", "decision": "skip", "reason": "lookup"}],
        entries=[{"project": "repo", "markdown": "### A\n\n- new", "keys": ["claude:a"], "extends": "### A", "win": False}])
    text = note.read_text()
    assert fenced not in text  # the fence was part of entry A, which was replaced whole
    assert "### Kept\n\n- kept" in text
    assert text.index("### Kept") < text.index("## Skipped") < text.index('"S" (claude): lookup')
