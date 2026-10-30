# Catch-up in Claude Code

Claude Code adds two tools to catch-up mode: the fanout plugin's `classify` for triage on a small day, and the Workflow tool for everything else. This file replaces catch-up step 1 ("List the candidates"); SKILL.md step 1, loading the config, still comes first. That way the digests reach your context only on a small day.

## 1. Save each day's candidates to a file

For each day, oldest first, run `journal.py candidates --date <day>` with its output redirected to a file in your scratch directory, such as `candidates-<day>.json`, and count its candidates with `jq '.candidates | length' <file>`. Keep the file's contents out of your context. A day with no candidates is done. Then pick the route:

| Candidates across the days asked for | Route |
|---|---|
| About ten or fewer, on one day | **Small day** |
| More, or several days | **Workflow** |

## Small day

Read the candidates with `jq -c '.candidates[]' <file>`; they carry every field catch-up step 1 describes. For catch-up step 2, send every candidate to the fanout plugin's `classify` tool in one call: labels `entry`, `lesson`, `skip` and `out of scope`, `reason: true`, the step's rules and the config's `scope` as the instructions, and each candidate's fields as one item, with its key as the id. If `classify` isn't loaded, use the step's subagent route. Steps 3 and 4 run as written.

## Workflow

The workflow script ships with this skill as `scripts/catch-up.workflow.js`. Haiku agents triage each day's candidates in chunks, read straight from the file. One agent per day then groups the entry sessions by piece of work and picks every project heading, reusing the note's; one agent per group drafts its entry against the existing day note, and new drafts over the length limit get a trim pass. It returns the decisions and drafted entries; you write the notes.

### 1. Gather each day's keys and note

For each day with candidates, get its keys in file order with `jq -c '[.candidates[].key]' <file>`, and its day note's path from `journal.py paths --date <day>`.

### 2. Run the workflow

The Workflow tool loads a `scriptPath` only from the working directory or an added directory, and the installed skill is usually outside both. Copy `scripts/catch-up.workflow.js` from this skill's directory into your scratch directory with `cp`, then call the Workflow tool with `scriptPath` set to the copy and these `args` as a JSON object, not a string:

```json
{
  "skill_dir": "<this skill's directory, absolute>",
  "scope": "<the config's scope, verbatim>",
  "days": [{"date": "2026-10-15", "candidates_file": "<absolute path>", "note": "<day note path>", "keys": ["claude:…", "omp:…"]}]
}
```

The agents name sessions by their index in `keys`, so copy each day's keys exactly as `jq` printed them.

Optional keys, each an object keyed by stage (`triage`, `group`, `draft`, `trim`):

- `agent_types`: a custom subagent type for a stage, such as a lean one the user keeps for cheap work. It sets that stage's model and effort.
- `models` and `efforts`: per-stage overrides. Defaults: triage and grouping on Haiku, drafting on the session's model at medium effort, trimming on Sonnet.
- `chunk_size`: candidates per triage agent, default 20.

Use the routing the user's own instructions name for subagent work, when they name one.

Done when the workflow returns. If it stops partway, run it again with `resumeFromRunId` and the same args: finished agents return their cached results.

### 3. Check the result

The result has one object per day: `decisions` (each judged candidate's key, decision and reason), `unjudged` (keys triage missed), `entries`, `lessons` and `problems`. For titles, read the candidates file with `jq -r '.candidates[] | [.key, .harness, .title] | @tsv' <file>`; the agents never copy them. Each entry has its `markdown`, its `project` heading, the `keys` it covers, its `words`, and `extends`: the heading of the existing entry it replaces, or empty for a new one.

- **Coverage:** the day's `decisions` keys plus `unjudged` match `jq -r '.candidates[].key' <file>` exactly (compare sorted lists with `comm -3`); a mismatch means a key was miscopied into `args`. Catch up any `unjudged` key through the small-day route.
- **Problems:** each names the failed triage chunk, group or draft. Rerun the failed part, or catch up its sessions the small-day way.
- **Skips:** read the skipped titles and reasons for a miss. A skip marked `shipped: true` committed, pushed or touched a pull request; make it an entry unless the reason holds, such as a journal run or a helper session.
- **Entries:** check each against the entry rules. Trim any new entry still well over the length limit. For each entry with `extends`, compare it with the entry it replaces and restore any fact, link or ticket key it dropped.
- **Lessons:** every lesson session comes back, with empty `markdown` when it held no lesson or when the agent returned nothing for it. Put the first kind in `skipped`. Leave a key that `problems` names as returned nothing out of both lists, so the next catch-up lists it again.

### 4. Write the notes

Write each day's note by catch-up step 4. An entry with `extends` replaces the entry under that heading; the rest go under their project, reusing the note's headings. Each entry's `keys` and each kept lesson's key go in `sessions`; skips, out-of-scope keys and lessons that held none go in `skipped`.

Done when every key in each day's candidates file is in that note's `sessions` or `skipped`, apart from keys `problems` named that you left for a rerun: `journal.py candidates --date <day> | jq -r '.candidates[].key'` prints only those.
