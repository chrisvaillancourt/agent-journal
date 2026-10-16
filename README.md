# agent-journal

An [Agent Skill](https://agentskills.io) that keeps a daily journal of what your agent sessions did and learned, across Claude Code and omp. It writes one Markdown note per day and a weekly review, for your own weekly reviews and for performance-review material.

It runs only when you ask: there are no hooks, background jobs or forks. The transcripts each harness already writes to disk are the source, so a catch-up hours or days later loses nothing, even after a session compacted.

## Modes

| Ask for | What happens |
|---|---|
| This session | The agent you're talking to writes the entry from its own context, which knows why things were done. |
| Catch-up for a day or range | A helper script lists the sessions active that day, a quick check (a small, fast model where the harness can run one) drops the ones not worth an entry, and the rest become entries. |
| Week | Rolls the week's day notes into highlights, wins written for a performance review, per-project summaries, lessons and open threads. Catches up missing days first. |

| Harness | Invoke |
|---|---|
| Claude Code | `/agent-journal`, `/agent-journal catch up yesterday`, `/agent-journal week` |
| omp | `/skill:agent-journal`, with the same arguments |
| Codex | `$agent-journal` (catch-up reads Claude Code and omp transcripts only) |

Or ask in plain words: "journal this session", "catch up the journal for Monday".

## Install

The helper script needs Python 3.11 or later and only the standard library; the skill runs it with [uv](https://docs.astral.sh/uv/) when it's installed, and `python3` otherwise. funes, a local index of past agent sessions, is optional: catch-up uses it to read sessions when it's there, and falls back to the transcripts otherwise.

From a checkout of this repository:

```sh
pnpm dlx skills add "$PWD" --skill agent-journal --agent claude-code universal --global --yes
```

`npx skills add …` works the same way. List the harnesses you use after `--agent`: `universal` installs to `~/.agents/skills`, which omp and Codex read; `claude-code` symlinks it into `~/.claude/skills`. The installed copy is separate from the checkout: rerun the command after changing the skill.

## Configure

Create `agent-journal/config.toml` in `$XDG_CONFIG_HOME` (or `~/.config`). The skill offers to write it on first use. Only `journal_dir` is required:

```toml
journal_dir = "~/journal"
day_note = "{date}-journal.md"        # filename patterns, relative to journal_dir (defaults shown)
week_note = "{week_start}-week.md"    # e.g. "{year}/{year}-W{week}.md" to nest by year
exclude_cwds = ["~/personal"]   # never journaled; temp dirs always excluded
include_headless = false              # claude -p and other scripted runs
notable_commands = ['\btracker\s+create\b']  # extra commands worth noting, beyond git/gh/glab/publish
scope = """
Work for my employer and the tooling I build for it. Not personal projects or hobbies.
"""
```

See [references/config.md](skills/agent-journal/references/config.md) for every field.

## What a day note looks like

```markdown
---
date: 2026-10-15
sessions: [claude:5f1c2a9e-…]
skipped: [omp:01a0f3c2-…]
tags: [agent-journal]
---

# Journal: 2026-10-15

## parser-service

### Fixed date parsing for European locales #win

- **Did:** Locale-aware parsing ships in #212; the nightly import stopped dropping about 3% of rows.
- **Decided:** Parse with the account's locale, not the server's.
- **Learned:** The fixtures were all en-US, which is why tests never caught it.

## Skipped

- "Look up the staging URL" (omp): quick lookup
```

The frontmatter lists every session judged, so rerunning catch-up skips them. Search `#win` across notes to gather performance-review material.

## The helper script

`skills/agent-journal/scripts/journal.py`:

- `candidates --date YYYY-MM-DD` prints JSON: each session active that local day with a digest of that day's prompts, edited files, commits and pull request commands, plus the sessions dropped by rule (subagents, headless runs, excluded directories, already judged) and why.
- `paths --date YYYY-MM-DD` prints the day note and week note paths from the configured patterns.
- `whoami` prints the current session's key. Claude Code exposes its id; in omp, pass a token you printed in an earlier command with `--nonce`.
- `config-path` prints where the config is read from.

## Limits

- Catch-up reads Claude Code and omp transcripts only. Sessions in other harnesses, Codex included, can still be journaled from inside the session.
- Weeks run Monday through Sunday.
- The lists of editing and shell tool names, and of housekeeping slash commands, match current Claude Code and omp; a harness release that renames them needs a script update.
- No automatic capture: nothing runs unless you ask (see decision 1 in [docs/decisions.md](docs/decisions.md)).

## Development

```sh
scripts/check.sh   # frontmatter, disclosure, privacy scan, skills CLI discovery, pytest
```

See [AGENTS.md](AGENTS.md) for the rules and [docs/decisions.md](docs/decisions.md) for why it's built this way.

## License

MIT
