# The config file

`journal.py config-path` prints where the config lives: `agent-journal/config.toml` in `$XDG_CONFIG_HOME`, or in `~/.config` when that's unset. It's TOML. To create it, ask the user where the journal should live, how they want notes named, and what's in scope, then write:

```toml
# Folder that holds the day and week notes. Required.
journal_dir = "~/journal"

# Note filenames, relative to journal_dir. Subfolders are fine; they're created as needed.
# Fields: {date} {year} {month} {day} {week} {iso_year} {week_start} {week_end}.
# A week note's fields describe its Monday. Defaults shown.
day_note = "{date}-journal.md"
week_note = "{week_start}-week.md"
# For example, nested by year: day_note = "{year}/{date}.md", week_note = "{year}/{year}-W{week}.md"

# Sessions whose working directory is at or under one of these are never journaled.
# Temp directories (/tmp, /var/folders and the system temp directory) are always excluded.
exclude_cwds = ["~/personal"]

# Also consider headless runs (claude -p and other scripted sessions). Default false.
include_headless = false

# Extra regular expressions for shell commands that leave a mark outside the session,
# such as your ticket tracker's CLI. Built in: git commit/push/merge/tag, gh and glab
# pull request/issue/release commands, and npm/uv/pnpm/cargo publish.
notable_commands = []

# In plain words: which sessions belong in this journal. Catch-up mode judges against it.
scope = """
Work for my employer, and the tooling I build for that work.
Not personal projects, hobbies or household questions.
"""

# Optional, for tests or unusual installs:
# claude_projects = "~/.claude/projects"
# omp_sessions = "~/.omp/agent/sessions"
```

- `~` and environment variables expand in every path.
- Only `journal_dir` is required. The script reads every field; `scope` it only passes to the Claude Code workflow, which judges sessions against it.
- Keep personal routing (paths, employer names, tool names) in this file, never in the skill.
- After changing it, run `journal.py paths` and `journal.py candidates` once: they report a bad template field or pattern.
