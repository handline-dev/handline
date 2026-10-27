# Improvement round

The team improves itself on a schedule instead of waiting for the founder to ask "why is this like this?". handline.architect runs
the round at 9:00 and 18:00, with the team check, and sends the founder one page a week.

## Daily round (9:00 and 18:00)

1. Read the last day's signals:
   - `hl handoff slow --since 1d`: the Slowed lines agents wrote in their handoffs. Count repeats across agents.
   - PRs that needed a second fix or failed CI: `gh pr list --state all --search "updated:>=<yesterday>"`, then
     `gh run list --status failure --created ">=<yesterday>"`, and tasks titled Re-review or Re-audit.
   - Load: the day's `load_limit_passed`, `park`, `dormant_block`, `task_timeout_exceeded` and `delivery_unknown` events
     (read-only query on `~/.handline/db.sqlite`).
   - Weekly use left unused at each reset that happened since the last round.
   - The founder's corrections: their messages of the day that redo or overrule a result ("다시", "왜", "아니").
2. Pick at most 3 causes, the ones that cost the most time or repeat the most. A cause already on an open task is skipped.
3. Open one task per cause as a spare draft: `--tag spare --tag improve --priority 3`, then `hl task draft T<n>`, assigned to the
   agent whose layer holds the cause, owner `handline`. Each body names the evidence and a done-criterion that can be checked.
   Spare drafts are started only to use weekly limit that would otherwise expire, so the round adds no cost and never delays
   real work.
4. Classify each in its body:
   - Internal efficiency (tests, tools, daemon speed, CI): goes through the auditor only.
   - Look, wording or rules: goes to the founder with a mock, as before.
5. Nothing found: open nothing and say nothing.

## Weekly page (Monday 9:00)

One `hl say human` message in plain Korean, numbers first, at most one screen:

- What changed: the improve tasks that landed, one line each.
- Load: hours held by the load limit, parks, timeouts, compared with the week before.
- Weekly limit: points left unused at reset, per account and in total.
- PR to merge: median hours from opening to merge, and the slowest PR.
- Decisions needed: only the items the founder must decide, each with its task number.

## Schedule

Two schedule entries for handline.architect (schedule.add, kept in its `agent.yaml`):

```
0 9,18 * * *   Improvement round (docs/improvement-round.md)
0 9 * * 1      Weekly page to the founder (docs/improvement-round.md)
```
