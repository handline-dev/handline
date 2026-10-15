# How agents should talk to each other: an experiment

Three haiku agents on a test daemon of their own (`HANDLINE_LIVE=1 python3 tools/agent_talk.py --design A|B|N --runs 3`). The
founder gives `talk.lead` a task: get the secret word each builder (`talk.a`, `talk.b`) keeps in a file only it can read, and
report both words to the founder. Each agent's ROLE.md states the design's rules. Run on 2026-10-15, main 804760406, account
gamma, Claude Code 2.1.300.

- **A, today:** `hl say` and tasks between agents; the lead may report with `hl say human`.
- **B, tasks only:** tasks between agents; the lead reports by closing the founder's task. `hl say` is refused at its
  permission prompt.
- **N, native founder talk:** as B between agents, but the lead reports in plain text in its own session, read from the
  transcript.
- **@name lines picked up by the daemon: not run.** Faking it without a daemon change would deliver each line as the
  founder's message, which is the thing under test.

## Results (3 runs per design)

| | A | B | N |
|---|---|---|---|
| Report with both words reached the founder | 3/3 | 2/3 | 3/3 |
| Time to that report, median | 26 s | 49 s (2 runs) | 40 s |
| Time to that report, min–max | 26–40 s | 37–62 s, 1 never | 27–62 s |
| Report channel used | task done ×3 | task done ×2, none ×1 | session text ×2, task done ×1 (against its rule) |
| Wrong sends (no edge, or a refused `hl say`) | 1 | 5 (3 `hl say` tries) | 2 (2 tries) |
| `hl say` calls (per run) | 3 (0, 0, 3) | 3 refused (0, 1, 2) | 2 refused (0, 1, 1) |
| Double reports | 0 | 0 | 1 |
| Lost words | 0 | 2 (the stuck run) | 0 |
| Session text blocks (stray) | 17 | 10 | 20 |
| of which echoes of a send | 16 | 10 | 15 |
| Cost per run, mean | $0.31 | $0.46 | $0.39 |
| Cost per run, min–max | $0.17–0.38 | $0.31–0.62 | $0.30–0.50 |
| Output tokens per run, mean | 1,772 | 1,872 | 2,277 |

Wrong sends count a message or task between agents that the job has no edge for, plus every `hl say` tried where it is
refused. An echo is session text written within a minute of the same agent sending something, naming the other agent or a
word. Three runs per design cannot separate the designs: B is slowest and costliest, but its ranges overlap A's and N's,
and its one failed run is the lead not closing the task, which has nothing to do with `hl say`.

Each run's counted row is in `docs/agent-talk-experiment.runs.jsonl`. The runs' raw event streams were not kept (they ran
without `--keep`), so the table can be re-added from the rows but not recounted from events; a rerun with `--keep` writes
`events.json` per run.

## What happened

- **The founder's report went by task, not `hl say`.** In A the lead reported by closing the founder's task in all three
  runs; `hl say` was called 3 times, all in one run, and none of those calls was the report. Where it was refused, agents
  still tried it 5 times in 6 runs, mostly to find out how to reach the founder.
- **The founder's task travels down a chain.** In 1 of 3 A runs and 2 of 3 B runs, the lead handed the founder's own task
  to a builder, who handed it on to the other, instead of opening one subtask each. With no `hl say`, whoever ends up holding
  the task has to report, and in an earlier batch (discarded for a harness bug, below) builders got stuck there.
- **The stuck B run:** the lead collected both words and wrote them in its session ("Both builders sent back their words:
  ..."), but never closed the founder's task. Under N that text would have reached the founder.
- **Numbers confuse:** in a trial run of A, the lead closed the founder's task with the evidence "request #2" instead of the
  words.
- **Session text is mostly talk about the work.** Every run had 3 to 10 assistant text blocks; 41 of 47 were echoes: "I
  handed T1 to talk.a", "The word is basil340, I marked T3 done". In N, builders also wrote their word to their own session
  instead of closing their task. If all session text reached the founder, each run would send 3 to 10 such lines, and the
  builders' lines would read as reports.

## Recommendation (for the founder to decide)

1. **Between agents, tasks are enough; `hl say` can go.** No run that finished needed it. B, without it, had the one
   failed run (2 words lost) and the slowest, costliest median, but that failure was the lead not closing the task, and
   three runs cannot show whether B is really slower. Before removing it: make "the agent that holds the founder's task reports
   by closing it" hold, by refusing a handoff of a founder-opened task to an agent below its owner (the owner opens a subtask
   instead). That rule would have prevented the chain stalls of the first batch; the stuck B run is a different miss (the
   lead had the words and never closed the task). Expect agents to keep trying `hl say` for a while, so its refusal should
   say what to do instead.
2. **Native founder talk: not with all session text.** Deliver to the founder only (a) text in a turn that a founder message
   started, which is a reply, and (b) text the agent marks for the founder, for example a line that starts with
   `@founder`. Everything else stays in the transcript, readable on demand. Both rules are untested here: no founder message
   started a turn in any run, so (a) never applied, and N's agents were told to write the report plainly, not to mark it,
   so (b) never applied. A run of N with the founder asking mid-job, and the marker in the role, would test both.
3. Keep `hl task done` evidence free text, and have the board notice quote it; avoid evidence that is only a number.

## Harness bug in the first batch

The first B and N batch refused every permission prompt whose command merely contained "hl say", so a handoff whose note
mentioned it, and `hl say --help`, were refused too. Two N runs never finished. Those runs are not in the table; B and N were
run again with the refusal matching only an `hl say` call.
