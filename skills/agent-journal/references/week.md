# Week mode

Roll a week of day notes into one review: what mattered, the wins, the lessons, and what's still open.

## 1. Pick the week and check it's complete

The week runs Monday through Sunday. Use the week the user names, or the one containing today. Run `journal.py paths --date <any day in the week>` for the week note's path and the week's dates, then `journal.py paths --date <day>` for each day's note path.

For each day up to today that has no note, count its candidates with `journal.py candidates --date <day> | jq '.candidates | length'`, which keeps the digests out of your context. If it has any, catch that day up first by following catch-up mode, unless the user said not to. Tell the user which days you caught up.

## 2. Write the week note

Write the week note at its `week_note` path, replacing an earlier version of the same week. Link each day note the way the journal directory's instructions say (wikilinks in an Obsidian vault, for example), and relative Markdown links otherwise.

```markdown
---
week: 2026-W42
from: 2026-10-12
to: 2026-10-18
tags: [agent-journal, weekly]
---

# Week of 2026-10-12

Days: Mon 2026-10-12, Tue 2026-10-13, Wed 2026-10-14, Thu 2026-10-15, Fri 2026-10-16 (each a link to its day note)

## Highlights

## Wins

## By project

## Lessons

## Open threads
```

- **Highlights:** 3 to 7 bullets, the outcomes that mattered most this week, most important first.
- **Wins:** every `#win` entry, rewritten for a performance review: the outcome, its impact, and a link to the evidence (pull request, ticket, doc). Add any outcome that clearly qualifies but wasn't marked, and say so.
- **By project:** for each project, 1 to 4 bullets summarizing the week's work, with links. Summarize; don't repeat the day entries.
- **Lessons:** the week's lessons, merged and deduplicated. Mark any that are worth promoting to the user's lasting memory or a repository's docs, and leave the promotion to the user.
- **Open threads:** when the user keeps another dated record of open items (a daily catch-up or status note, say), link the latest one's open items instead of recomputing them, and list only the week's open threads it doesn't hold. Otherwise list the open items from the week that still apply. Give each listed item a disposition; check items you can verify (a pull request merged, a ticket closed) and drop the finished ones.

Done when the week note exists, every day with activity has a day note, and every win links its evidence.
