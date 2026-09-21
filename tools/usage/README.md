# Where the usage goes

Read-only measurements of what the fleet spends, from the Claude transcripts
and the daemon's records. Nothing here changes policy and nothing in the picking
rules reads the numbers. Each answers one question and rests on what is named.

- `extract.py calls.pkl` - every API call of every account, once per request
  id. Rests on the transcripts being complete for the days asked about.
- `cache_gaps.py calls.pkl` - how much cache-write goes to rebuilding a context
  after an idle hour or a session start, against mid-session growth. Rests on
  the 1-hour cache life the transcripts show.
- `by_agent_turn.py calls.pkl` - usage by agent and by turn, and what the
  expensive turns have in common. Rests on the weights in `W`, which are
  fitted, and on the working directory naming the agent.
- `fit.py [interval_s]` - what a token of each class costs the plan, with a
  bootstrap interval, from `usage_samples` against `token_calls` (which the
  daemon records per call, 60 days). Needs numpy. The first fit, on 56
  intervals over 7 hours, gave cache read 0-65% of usage: read the interval,
  not the point, and rerun it as the record grows.
