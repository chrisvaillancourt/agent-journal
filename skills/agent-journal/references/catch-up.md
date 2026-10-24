# Catch-up mode

Journal every session worth an entry on one day, or each day of a range, from the transcripts on disk. Run the steps once per day, oldest first. With no date given, use today.

## 1. List the candidates

Run `journal.py candidates --date <YYYY-MM-DD>`. It prints JSON:

- `note`: the day note's path.
- `candidates`: sessions active that day that no rule excluded. Each has a `key`, `harness`, `title`, `cwd`, `repo`, the day's `prompts` (shortened), `user_turns`, `tool_calls`, `files_edited`, `notable_commands` (commits, pushes, pull request and ticket commands), the day's `last_reply`, a `funes_id` and the `transcript` path. Only that day's activity is counted, so a long session shows up on each day it ran.
- `filtered`: sessions dropped by rule (already journaled or skipped, excluded directory, headless run, no typed prompts), each with its reason.

No candidates means the day is done: say so and stop.

## 2. Decide which are worth an entry

Give each candidate one decision:

- **entry**: it changed something lasting (code, docs, tickets, pull requests, messages sent, a decision made) or produced a finding worth recalling.
- **lesson**: little work, but a lasting lesson (how a tool really behaves, a gotcha, a technique). It gets a one-line entry.
- **skip**: a quick lookup or status check, navigation ("find the worktree for…"), an accidental or abandoned session, or a helper session doing part of another candidate's work (its prompt is a delegated brief, such as one that opens with `<file name=` or "Complete assignment"; the parent's entry covers it).
- **out of scope**: outside the config's `scope`.

When unsure between entry and skip, choose entry if the session edited files or ran notable commands, and skip otherwise.

This is a quick judgment on short digests, so use a small, fast model when your harness can run one as a subagent: send the candidates in batches of about ten, with the config's `scope`, the decision rules above, and the fields `key`, `title`, `cwd`, `repo`, `prompts`, `files_edited`, `notable_commands` and `last_reply`. Ask for one line of JSON per candidate: `{"key", "decision", "project", "reason"}`, with `reason` under ten words. Without subagents, decide inline. Either way, review the result: a session that pushed commits or opened a pull request is rarely a skip.

## 3. Gather what each entry needs

For each **entry** session, find the outcome, decisions and lessons. The digest's prompts and last reply are a start, not enough. In order of preference:

1. `funes sketch <funes_id>` (or the funes MCP `sketch` tool) for a digest of the whole session, then `funes get` for the stretches that matter. For a session that spans days, keep to the day by the timestamps: every `sketch` hit and `get` turn prints its timestamp beside its seq, so use hits that fall on the day, and pass `get` the seqs of hits on either side of the day's boundaries as `--from`/`--to`. `candidates` gives the day's `first_ts`/`last_ts`, not seqs.
2. Without funes, read the transcript with `jq`, filtering to the day's timestamps.
3. `git log` in the session's repository for commit subjects, and the pull request or ticket for numbers and links.

When your harness can run subagents, give each entry session (or group of sessions on the same work) to one, on a capable model since the writing matters. Pass the entry rules from SKILL.md step 2 and the session's digest, and ask for the finished entry with its project. Merge entries on the same work before writing.

**Lesson** sessions need only their one line: read just enough to state the lesson correctly.

A session that ran a routine with its own dated record (a daily catch-up note, meeting notes) gets an entry only for the decisions and lessons that record doesn't hold, with a link to it. Don't restate the record.

Before writing, check every drafted entry against the entry rules, length above all: subagents tend to write long. Trim to the most important points rather than sending a draft back.

## 4. Write the day note

Create or update the note as SKILL.md step 2 describes:

- Add each **entry** and **lesson** under its project.
- Put every judged key in the frontmatter: entries and lessons in `sessions`, skips and out-of-scope sessions in `skipped`.
- Under `## Skipped`, list each skipped session's title, harness and reason, so the user can spot a miss. Out-of-scope sessions get no detail: add one line counting them ("2 out-of-scope sessions").
- Leave the `filtered` sessions out of the note: they weren't judged, and a rerun filters them the same way.

Done when each candidate's key is in `sessions` or `skipped`, and every entry follows the rules.
