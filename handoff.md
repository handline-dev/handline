# handoff - orcapipe.expender, #1402 (statusLine)

Stopped at the founder's request (#1447) with usage needed elsewhere. Nothing
is half-written: both commits are whole, the suite is green, and the branch is
not merged.

    56a4e6e  feat: the statusLine shows the agent instead of collecting from it
    0b6b81a  feat: read a session's own file instead of its status line

`tests/test_ocp.py`: **2588 passed, 0 failed** (`/usr/bin/python3`, 100s).
11 mutation controls, one per new condition, each caught by a different check.

## Done

- `ocp/collect.py` - context, session identity, model and cost pulled from the
  session's own transcript. Checked against the live collector on 19 agents:
  identical to the token on all 14 that had a figure, better on 4 (three
  cleared sessions read 0% where the push said nothing; `harbor`'s live session
  had been invisible to the push for five hours).
- `ocp/statusline.py` + `libexec/statusline.sh` - the slot is a display now.
- Hooks (`Stop`, `SessionStart`, `PostCompact`, …) refresh the snapshot and the
  line per turn; the daemon tick is the net under them.
- `daemon.poll_usage` - rate limits from the endpoint, one account per tick,
  only when a turn happened since the last reading, a window ended, or the
  cadence came round.
- Deleted: `libexec/statusline-collect.sh`, the statusLine install, its doctor
  checks, **jq as a dependency**.
- Docs: D11, design 01-infra §4 rewritten, 03-vendor-driver `collector_kind`
  now `pull` for both vendors.

## Left, in the order I would do it

1. **Review and merge.** Not reviewed by anyone yet, and CLAUDE.md wants a
   diff review for hacky workarounds and overlong comments before it merges.
2. **One deliberate wording change I had queued and did not make:**
   `doctor.py`, beside `r.add(WARN, "shared statusLine is not ours", ...)` -
   a line saying the WARN is deliberate (nothing ocp decides reads that slot,
   so someone using it for their own line is their call), so a later hand does
   not "fix" it back to FAIL.
3. **After merge, run `ocp init`** on the live home. Until it runs, the old
   collector stays installed and running from `shared/.claude`, and `ocp
   doctor` says "status line not installed" (1 FAIL, 1 WARN - measured, those
   two rows are the only new ones).
4. **Then watch one thing for a few ticks:** every account's `rl_at` should
   start moving on its own (`ocp usage`, `ocp account list`). Today four of
   five accounts read "no usable rate_limits" because nothing has asked them.
   If they stay unknown, the poll is not reaching the endpoint - look at
   `usage-poll:<acct>` / `usage-refresh:<acct>` cursors and the daemon log's
   `usage:` lines before assuming the design is wrong.
5. **Ask the founder what the line should say.** What is there is my default,
   and it is one function (`statusline.render`). They asked for the slot back
   for "somewhere else" and may want it spent differently.

## Deferred deliberately, each needing a decision rather than work

- **`probe.py` (187 lines) and `probe_stale_accounts` (~55).** Their conditions
  are now nearly unreachable, but they are the only way to learn usage when the
  endpoint cannot be asked at all. Deleting them is the founder's call, and I
  told them so rather than counting the lines as saved.
- **`status.py`'s `idle_floor` (~40 lines).** Same shape: it exists because a
  quiet account's reading aged, which the poll fixes - but only while the poll
  answers.
- **`CTX_SIZE` is a table** (`collect.py`), seeded from what the statusLine
  itself reported across the fleet. An unmeasured model gets no percentage
  rather than a guessed one. If a model ever runs on a window that is not 1M,
  this is the line to fix.
- **codex.** The pull is the shape codex needs (rollout JSONL carries context,
  identity and rate limits - 1,868 of 1,869 files measured). Nothing here is
  codex-specific yet; Q2 is still the vendor driver.
