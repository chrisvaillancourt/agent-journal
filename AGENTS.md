# AGENTS.md

This repository is the `agent-journal` agent skill: a daily journal of what agent sessions did and learned, written on request from the current session or caught up from Claude Code and omp transcripts. Read this file first, then open only the documents your task needs.

## Where to look

| Task | Read |
|---|---|
| Understand what the skill tells agents to do | [skills/agent-journal/SKILL.md](skills/agent-journal/SKILL.md) |
| Change the day note or entry rules | SKILL.md step 2 |
| Change how a day is caught up or how sessions are judged | [references/catch-up.md](skills/agent-journal/references/catch-up.md) |
| Change the weekly review | [references/week.md](skills/agent-journal/references/week.md) |
| Change the config format | [references/config.md](skills/agent-journal/references/config.md), `load_config` in the script, and the README's Configure section |
| Change which sessions are listed, or how transcripts are read | [scripts/journal.py](skills/agent-journal/scripts/journal.py) and [tests/test_journal.py](tests/test_journal.py) |
| Know why something is the way it is, before changing it | [docs/decisions.md](docs/decisions.md) |
| Install and use | [README.md](README.md) |

## Rules

- **The installed copy is separate.** `skills add … --agent claude-code universal --global` copies the skill to `~/.agents/skills` and symlinks it into `~/.claude/skills`. Editing this checkout doesn't change it. Ask the user before reinstalling.
- **Keep the skill harness-neutral.** Frontmatter uses only the open spec's fields. The body names actions, not one harness's tool names, and uses no harness variables such as `${CLAUDE_SKILL_DIR}`. `scripts/check.sh` enforces the frontmatter part.
- **Keep the skill and docs publishable.** No personal paths, account names, emails, organization names, or examples copied from real sessions. Personal routing belongs in the user's config file. `scripts/check.sh` scans for generic patterns, plus personal terms listed one regex per line in an untracked `.privacy-patterns.local`; never commit those terms.
- **Respect the disclosure split.** SKILL.md holds what every run needs. A reference file holds one mode, is reached by a pointer in SKILL.md that names its condition, and links to no other reference file.
- **The script stays standard-library Python,** run with `uv run --no-project`. A transcript format change gets a fixture test that fails without the fix.
- **Manual only.** No hooks, background jobs or session forks until the manual version has proven itself (decision 1). Raise it with the user before adding any.
- **Don't reverse a decision silently.** If a change contradicts [docs/decisions.md](docs/decisions.md), raise it with the user first. Record new or changed decisions there.
- **Update every reference when something changes:** the README, AGENTS.md, docs, tests and the table above.

## Before you finish

1. Run `scripts/check.sh`. All checks must pass.
2. Commit with sentence-case imperative messages, no prefixes. Explain why in the body, and leave out tool attribution.
