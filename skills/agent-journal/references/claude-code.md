# Catch-up in Claude Code

Claude Code adds two tools to catch-up mode: the fanout plugin's `classify` for triage on a small day, and the Workflow tool for everything else. This file replaces catch-up step 1 ("List the candidates"); SKILL.md step 1, loading the config, still comes first. That way the digests reach your context only on a small day.

## 1. Prepare the days

Run `journal.py prepare --from <first day> --to <last day> --out <your scratch directory>` (`--to` defaults to the first day). It saves each day's candidates to `candidates-<day>.json`, copies the workflow script beside them, writes the Workflow args to `args.json`, and prints `script_path`, `args_file`, each day's count and the `total`. Keep the candidates files out of your context. A day with no candidates is done. Then pick the route:

| Candidates across the days asked for | Route |
|---|---|
| About ten or fewer, on one day | **Small day** |
| More, or several days | **Workflow** |

## Small day

Read the candidates with `jq -c '.candidates[]' <file>`; they carry every field catch-up step 1 describes. For catch-up step 2, send every candidate to the fanout plugin's `classify` tool in one call: labels `entry`, `lesson`, `skip` and `out of scope`, `reason: true`, the step's rules and the config's `scope` as the instructions, and each candidate's fields as one item, with its key as the id. If `classify` isn't loaded, use the step's subagent route. Steps 3 and 4 run as written.

## Workflow

The workflow script is `scripts/catch-up.workflow.js`. Haiku agents triage each day's candidates in chunks, read straight from the files. One agent per day then groups the entry sessions by piece of work and picks every project heading, reusing the note's; one agent per group drafts its entry against the existing day note, and new drafts over the length limit get a trim pass. It returns the decisions and drafts, and `journal.py merge` writes them into the notes.

### 1. Run the workflow

Read `args_file` and call the Workflow tool with `scriptPath` set to the printed `script_path` and `args` set to the file's contents, as a JSON object, not a string. The copy matters: the Workflow tool loads a script only from a directory the session can read, and the installed skill is usually outside them. Pass the args exactly as written; the agents name sessions by their index in each day's `keys`.

You may add routing to the args, each an object keyed by stage (`triage`, `group`, `draft`, `trim`):

- `agent_types`: a custom subagent type for a stage, such as a lean one the user keeps for cheap work. It sets that stage's model and effort.
- `models` and `efforts`: per-stage overrides. Defaults: triage and grouping on Haiku, drafting on the session's model at medium effort, trimming on Sonnet.
- `chunk_size`: candidates per triage agent, default 20.

Use the routing the user's own instructions name for subagent work, when they name one.

Done when the workflow returns. If it stops partway, run it again with `resumeFromRunId` and the same args: finished agents return their cached results.

### 2. Review the result

Run `journal.py merge <the workflow's output file>`, without `--write`. It checks each day's keys against its candidates file and reports what it would add, the headings it would create, the keys it leaves for the next catch-up (`rerun`: unjudged sessions, failed drafts, lessons the agent didn't write) and any warnings. A day whose keys don't match is refused.

Then read the result itself:

- **Problems:** each names the failed triage chunk, group or draft. Those sessions are in `rerun`; catch them up again once the rest is written.
- **Skips:** read the skip reasons for a miss; titles are in the candidates file (`jq -r '.candidates[] | [.key, .title] | @tsv' <file>`). A skip marked `shipped: true` committed, pushed or touched a pull request; make it an entry unless the reason holds, such as a journal run or a helper session.
- **Entries:** check each against the entry rules and SKILL.md's rule against secrets and other people's personal data. Trim any new entry still well over the length limit. For each entry with `extends`, compare it with the entry it replaces and restore any fact, link or ticket key it dropped.
- **Headings:** compare the new headings `merge` reports with the ones the journal already uses, and map any stray one with `--heading OLD=NEW`.

To change an entry or a decision, edit the result JSON (for example with `jq` into a new file) and merge that.

### 3. Write the notes

Run `journal.py merge <result> --write`, with any `--heading` mappings. It adds each entry under its heading (replacing the entry it extends), adds kept lessons, appends skips to `## Skipped` with their titles and reasons, counts out-of-scope sessions without detail, and records every judged key in `sessions` or `skipped`. Running it twice adds nothing twice.

Done when `journal.py candidates --date <day> | jq -r '.candidates[].key'` prints only the `rerun` keys for each day. Catch those up next.
