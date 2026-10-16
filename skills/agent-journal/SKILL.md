---
name: agent-journal
description: Keeps a daily journal of what agent sessions did and learned. Journals the current session, catches up a day's Claude Code and omp sessions after a quick check of which are worth an entry, or rolls a week of entries into a review with wins for performance reviews. Use when the user asks to journal or log this session, catch up or backfill the journal for a day, or write the weekly journal review. Not for task handoffs, ticket status or saving work in progress.
license: MIT
---

# Agent journal

The journal is one note per day, plus a weekly review, in the user's journal directory. Entries say what got done, what was decided and what was learned, with links, so the user can run weekly reviews and write performance reviews from them. The journal is a record, not a tracker: ticket and pull request status stays in those systems, and handoffs for unfinished work belong to whatever the user uses for handoffs.

Pick the mode from the request:

| Request | Mode |
|---|---|
| "journal this", "log this session", no further detail | **This session**: steps 1, 2 and 3 below |
| "catch up the journal", "journal yesterday", a date or date range | **Catch-up**: step 1, then [references/catch-up.md](references/catch-up.md) |
| "weekly review", "journal week", "roll up the week" | **Week**: step 1, then [references/week.md](references/week.md) |

The helper script is `scripts/journal.py` in this skill's directory. Run it with `uv run --no-project <this skill's directory>/scripts/journal.py <command>`, or with `python3` (3.11 or later) where uv isn't installed; it needs only the standard library.

## 1. Load the config

Run `journal.py config-path` and read the file it prints. It sets where notes go (`journal_dir`, plus optional filename patterns), which directories are never journaled (`exclude_cwds`) and, in plain words, what belongs in the journal (`scope`). If the file is missing or the user wants to change it, read [references/config.md](references/config.md).

Then read the instructions that govern the journal directory (an `AGENTS.md` or `CLAUDE.md` in it or above it) and follow them for naming, links and commits.

Done when you know the journal directory, the scope and the directory's rules.

## 2. The day note

A day note covers one local day. Its path is `day_note` in the output of `journal.py paths --date <YYYY-MM-DD>`; the config decides the folder and filename, so never build the path yourself. Create the note from this shape when it doesn't exist:

```markdown
---
date: 2026-10-15
sessions:
  - claude:5f1c2a9e-7b40-4d1e-9c3a-2e8f6b1d0a47
skipped:
  - omp:01a0f3c2-9d41-7000-8e2b-6a1c4f7d2b90
tags: [agent-journal]
---

# Journal: 2026-10-15

## parser-service

### Fixed date parsing for European locales #win

- **Did:** Locale-aware parsing ships in [#212](https://example.com/pr/212); the nightly import stopped dropping about 3% of rows.
- **Decided:** Parse with the account's locale, not the server's, because imports run on shared workers.
- **Learned:** The fixture data was all en-US, which is why tests never caught it.
- **Open:** Backfill the dropped rows: deferred to [PROJ-88](https://example.com/browse/PROJ-88).

## Skipped

- "Look up the staging URL" (omp): quick lookup
```

Rules for entries:

- **One entry per piece of work,** not per session. Sessions on the same work share an entry; every session's key still goes in `sessions`.
- **Group under the project:** the repository name, or the area of work when there's no repository.
- **Outcomes, not steps.** Write for the user six months from now: what changed and why it mattered, with numbers, pull requests, ticket keys and commits. Leave out the play-by-play: who reviewed what in which round, costs of individual tool runs, every commit hash.
- **Keep it short.** One bullet per label, each at most three sentences; an entry fits in about 150 words. The linked pull request, ticket or note holds the detail; the entry says what changed and points there. When there's more to say, keep the most important and drop the rest.
- **Each line is optional except Did.** Drop Decided, Learned and Open when there's nothing real to say. Open items each carry a disposition (do, defer, drop).
- **Learned means it would change how the user works next time:** a gotcha, a technique, how a system really behaves. A fact that is now in the code or docs isn't a lesson.
- **Mark a win** with `#win` at the end of the heading when the outcome is worth citing in a performance review: something shipped, someone unblocked, a measurable improvement, a problem found before it hurt.
- **Link instead of copying.** Record events with their date ("opened #212"), never live status ("#212 is open"), which goes stale.
- **Never write secrets,** credentials or other people's personal data.
- **Update in place.** When an entry for the same work already exists on that day, extend it instead of adding another.
- `sessions` lists every session key with an entry; `skipped` lists every key judged not worth one. Both keep a rerun from repeating work.

## 3. Journal this session

1. Find this session's key: run `journal.py whoami`. If it reports the session wasn't found, make up a fresh token such as `aj-` followed by eight random characters, print it with `echo <token>`, then run `journal.py whoami --nonce <token>` as a separate command.
2. Get today's note path with `journal.py paths` (it defaults to today's local date).
3. If the key is already in that note's `sessions`, update its entry. Otherwise write a new one from what you know of this session. Check facts you'll cite (commit subjects, pull request numbers, ticket keys) with `git log` or the system that holds them, rather than from memory.
4. Add the key to `sessions`.

Done when the entry is written and the key is in the frontmatter.

## Finish (every mode)

1. If the journal directory is in a git repository, commit the notes you wrote, following its rules. Name their paths in the commit so nothing else staged rides along.
2. Report the note's path, the entries written or updated (headings only), and any wins marked. In catch-up mode, also list what was skipped so the user can spot a miss.
