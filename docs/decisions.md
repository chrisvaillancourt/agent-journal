# Design decisions

Each entry records what was decided, why, the alternatives, and when to revisit it. Written 2026-10-15.

## Contents

1. [Manual and on-demand only](#1-manual-and-on-demand-only)
2. [Read transcripts directly to pick sessions](#2-read-transcripts-directly-to-pick-sessions)
3. [Local days, counted by activity](#3-local-days-counted-by-activity)
4. [Rules first, then a quick model judgment](#4-rules-first-then-a-quick-model-judgment)
5. [The day note's frontmatter is the only state](#5-the-day-notes-frontmatter-is-the-only-state)
6. [One entry per piece of work, outcomes not steps](#6-one-entry-per-piece-of-work-outcomes-not-steps)
7. [Daily notes are the archive; the week note is what gets read](#7-daily-notes-are-the-archive-the-week-note-is-what-gets-read)
8. [Finding the current session](#8-finding-the-current-session)
9. [Out-of-scope sessions leave no detail](#9-out-of-scope-sessions-leave-no-detail)
10. [Note paths come from the script](#10-note-paths-come-from-the-script)
11. [Read compacted turns back; link open items kept elsewhere](#11-read-compacted-turns-back-link-open-items-kept-elsewhere)
12. [Claude Code catches up large ranges with a Workflow](#12-claude-code-catches-up-large-ranges-with-a-workflow)

## 1. Manual and on-demand only

**Decision:** The journal is written when the user asks: from inside a session, or as a catch-up for a day or range. No hooks, scheduled jobs or session forks.

**Why:** Automatic session logs tend to become append-only noise nobody reads. Proving that the entries get read comes before automating them. Automatic capture also has real gaps: Claude Code's `SessionEnd` hook has a 1.5-second default budget and can't block exit, idle sessions never fire it, and whether a detached fork survives the CLI exiting is undocumented. Catch-up reads the transcript files, which keep the full history even after compaction, so batching later loses nothing a hook would have kept.

**Alternatives:** A `PreCompact` or `SessionEnd` hook that starts `claude -p --resume <id> --fork-session` in the background, plus an omp extension on `session_before_compact`/`session_shutdown`; a `Stop` hook that blocks and asks the session to spawn a fork subagent; a nightly scheduled digest.

**Revisit when:** The user has read the week notes for a couple of weeks and wants entries without asking, or catch-up regularly misses context that only the live session had.

## 2. Read transcripts directly to pick sessions

**Decision:** `journal.py candidates` reads the Claude Code and omp transcript files itself. funes is used afterwards, for detail on the sessions picked.

**Why:** funes `sessions` filters by start date only and lists subagent sessions as their own rows: on a sample day it returned about 160 rows, most of them subagents, and missed long sessions that started earlier. Reading the files gives per-day activity, the typed prompts, edited files and notable commands in one pass, with no dependency.

**Alternatives:** funes `sessions` plus `sketch` for every row (slow, and the filtering problems above).

**Revisit when:** funes gains activity-date filtering and a parent/subagent flag, or either harness changes its transcript layout.

## 3. Local days, counted by activity

**Decision:** A day is the user's local calendar day. A session counts on every day it had activity, with only that day's activity in its digest.

**Why:** Journals are read by day. Sessions often run past midnight UTC, or for several days.

**Revisit when:** Never expected.

## 4. Rules first, then a quick model judgment

**Decision:** The script drops sessions by rule (subagent transcripts, headless runs, temp and excluded directories, sessions with no typed prompts, already judged). The agent then judges the rest as entry, lesson, skip or out of scope, using a batch classifier tool where the harness has one, else a small, fast model as a subagent where the harness can run one, and checks the result.

**Why:** Rules are free and certain for the obvious cases. Whether a session mattered isn't a rule: a one-question session can carry a lasting lesson, and a long one can be a dead end. The digest is short, so a small model can judge it cheaply. On a close call the bias is toward an entry for sessions that edited files or ran notable commands, because a missed performance-review item costs more than one extra entry. Skipped sessions are listed in the note so the user can spot a miss.

**Alternatives:** Rules only (misses lessons from short sessions); the main model judges everything (costly with 30+ sessions a day).

**Revisit when:** The skipped lists show a pattern of misses, or a rule would catch something the model judges every time.

## 5. The day note's frontmatter is the only state

**Decision:** Each day note lists the session keys it judged under `sessions` and `skipped`. Nothing else tracks what's been journaled.

**Why:** No separate state file to drift or lose. Reruns are safe. The user can see and edit what was judged.

**Revisit when:** A range catch-up gets slow from reading many notes (not expected at one note a day).

## 6. One entry per piece of work, outcomes not steps

**Decision:** Sessions on the same work share an entry. Entries say what changed and why it mattered, with links, and record events with dates instead of live status.

**Why:** The user often splits one piece of work across several sessions and harnesses. A per-session log repeats itself and reads as a play-by-play. Live status ("PR is open") goes stale and duplicates the systems that own it.

## 7. Daily notes are the archive; the week note is what gets read

**Decision:** Day notes accumulate and aren't meant to be reread. The weekly review is the reading surface: highlights, wins rewritten for a performance review, per-project summaries, lessons and open threads.

**Why:** Logs grow faster than anyone reads them: the volume goes into the archive, and the reading happens once a week.

**Revisit when:** The week notes aren't being read either; then the experiment has failed.

## 8. Finding the current session

**Decision:** `whoami` uses `CLAUDE_CODE_SESSION_ID` in Claude Code. Elsewhere, the agent prints a fresh token in one command and passes it to `whoami --nonce` in the next; the script finds the transcript that contains it.

**Why:** omp doesn't expose its session id to shell commands (checked in its source on 2026-10-15). A token the agent printed is in its own transcript by the next command, whatever the harness.

**Revisit when:** omp exposes a session id or file to shell commands.

## 9. Out-of-scope sessions leave no detail

**Decision:** Sessions outside the config's `scope` get only their key in `skipped` and one counting line in the note, with no title or reason.

**Why:** The journal may live somewhere that must not hold out-of-scope material, such as a work notes vault that excludes personal matters.

## 10. Note paths come from the script

**Decision:** The config sets the journal folder and, through `day_note` and `week_note` patterns, each note's filename and subfolder. The skill gets every note path from `journal.py paths` and never builds one itself. Tool-specific signals, such as a ticket tracker's CLI, go in the config's `notable_commands`, not the code.

**Why:** Other users organize notes differently (nested by year, ISO week names), and the script needs the same path to read a day note's frontmatter. One function computing paths keeps the skill and the script from disagreeing.

## 11. Read compacted turns back; link open items kept elsewhere

**Decision:** "Journal this session" reads the session's early turns back (funes `sketch`, then `get` or `scan`, or the transcript file) when the session was compacted or resumed, before writing. Catch-up keeps a multi-day session to the day by the timestamps funes prints beside each seq, not by seqs from `candidates`, which emits only timestamps. The week note's open threads link the latest dated record of open items the user already keeps (a daily catch-up or status note) and list only threads it doesn't hold.

**Why:** An audit of real use (2026-10-24) found three gaps. Writing "from what you know of this session" after a compaction works from a summary that drops ids, errors and commands. Catch-up told the agent to pass seqs it had no way to get. The week note recomputed open items that a daily catch-up note already held, so the two could disagree.

**Alternatives:** have `journal.py` emit funes seqs (it would have to assume funes numbers turns in transcript record order, which isn't documented).

**Revisit when:** funes accepts timestamps for `get`, or the week note's open threads stop matching the linked record.

## 12. Claude Code catches up large ranges with a Workflow

**Decision:** In Claude Code, a catch-up of several days, or of a day with more than about ten candidates, runs `scripts/catch-up.workflow.js` through the Workflow tool. Haiku agents triage each day's candidates in chunks of 20, reading them from a file the main agent saved; one agent groups a day's entry sessions by piece of work and picks every project heading for the day, reusing the note's; one agent per group drafts its entry, and one per day writes the lesson lines. Drafting agents read the existing day note and say which entry they extend, and new drafts over 170 words get one trim pass (an extended entry is exempt, since a trim would cut the note's existing facts). `journal.py prepare` saves the candidates and builds the args; the main agent checks the result, and `journal.py merge` writes the notes after checking every key against the saved candidates, leaving unfinished sessions for the next catch-up. Agents name sessions by their index in the day's key list, which the main agent passes in; the script maps indexes back to keys. A skip that shipped commits or a pull request is flagged for the main agent, not promoted. Claude Code's steps live in `references/claude-code.md`, which SKILL.md points to under a "Claude Code only" condition, so other harnesses never load them. In Claude Code it replaces catch-up step 1: each day's candidates go straight to a file, so the main agent never reads the digests.

**Why:** Passing digests inline makes the main agent write each one out as tool input: the 4 days of 2026-10-19 to 2026-10-22 held 78 candidates and about 285 KB of digests. Drafting agents started ad hoc inherit the session's model and effort unless someone remembers to route them, and each one's tool output comes back into the main context. The Workflow keeps the digests and transcripts in the agents, checks every return against a schema, sets each stage's model, and resumes an interrupted run from its cache. One agent per piece of work, not per transcript, matches decision 6: a group's sessions are one story, and splitting them would leave the main agent merging partial drafts. The pilot on 2026-10-19 to 2026-10-22 (59 agents, 3.6M subagent tokens, under 4 minutes) showed the rest: a Haiku triage agent miscopied one session key, auto-promotion turned a journal run into an entry, drafts ran 140 to 200 words, and drafts that never saw the day note couldn't extend its entries or reuse its project headings. A second run with drafts reading the note fixed coverage and length (92 to 156 words) but still split one day across nine headings and miscopied a title, so headings moved to the one agent that sees the whole day, and titles come from the candidates file.

**Alternatives:** One subagent per transcript (partial drafts of shared work); one Workflow stage that both triages and drafts (no grouping across a day's sessions); shipping custom agent definitions with the skill (a skill can't install them, so the script takes `agent_types` instead); routing keys in the config file (the user's own instructions already say how to route subagents).

**Revisit when:** Another harness gets a comparable orchestration tool, triage chunks miss sessions or disagree on grouping, or the per-agent context overhead outweighs the main-context savings on small ranges.
