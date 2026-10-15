# How agents should talk to each other: an experiment

Three haiku agents on a test daemon of their own, never the live daemon or team. The founder gives `talk.lead` a task: get the
secret word each builder (`talk.a`, `talk.b`) keeps in a file only it can read, and report both words to the founder.

## Read first: round 1 did not test what it meant to

A structured session (the kind the daemon starts here) gets neither its agent's model nor its context: the daemon starts
`claude` with no `--model` and no system prompt, so `agent.yaml`'s `model: haiku` and the agent's ROLE.md never reach it. In
round 1's 46 transcripts Sonnet answered 295 times, Opus 53 and Haiku never, and the design rules (A, B, N) appear in none.
Round 1's numbers therefore compare three runs of one untold setup, not three designs. A daemon fix is in review; until it lands, round 2 works
around it in the harness: each agent's checkout has its role as the project's `CLAUDE.md` and `"model": "haiku"` in its
project settings. Every round 2 run was checked to have answered with `claude-haiku-5-5` only.

## Round 2: can agents keep their session text for the founder?

`HANDLINE_LIVE=1 python3 tools/agent_talk_round2.py --runs 6 --keep DIR` (and `--runs 3 --conditions md`). Run on
2026-10-15, Claude Code 2.1.300.

Channels assumed in every condition: an agent talks to the founder by writing in its own session, and every text block reaches
the founder except one that is exactly `noop`. `hl say human` answers "use the chat instead": the daemon has no such reply, so
a shim in front of `hl` gives it. Agents talk to each other with `hl say <agent>` and tasks. Each condition adds one line to
every role:

- **none:** nothing, round 1's baseline.
- **rule:** "After you send to another agent, do not repeat it in your session; session text is only for the founder."
- **noop:** "When you have nothing to tell the founder, end your turn with exactly `noop`."
- **md:** as noop, and every `hl say` body goes in Markdown through `--file -` (3 runs, a side check).

In every second run the founder asks the lead mid-job, in its session, to reply with a code (`ACK` and three digits), so a
reply to the founder is tested.

| | none | rule | noop | md |
|---|---|---|---|---|
| Runs (founder asked mid-job) | 6 (3) | 6 (3) | 6 (3) | 3 (1) |
| Report with both words reached the founder | 6/6 | 6/6 | 6/6 | 3/3 |
| First report's channel | 1x session text, 5x task done | 4x session text, 2x task done | 3x session text, 3x task done | 1x session text, 2x task done |
| Time to the report, s, median (min-max) | 98 (74-158) | 63 (47-114) | 148 (57-196) | 95 (42-130) |
| Missed reports | 0 | 0 | 0 | 0 |
| Founder's question answered | 3/3 | 3/3 | 3/3 | 1/1 |
| ... in the turn the question started | 3/3 | 2/3 | 3/3 | 0/1 |
| Text in founder-started turns (of it, reports) | 9 (3) | 9 (1) | 10 (1) | 2 (0) |
| Report still reached the founder with only those turns and task done | 5/6 | 3/6 | 4/6 | 2/3 |
| Echoes reaching the founder, per run (total) | 4 (2-5) (23) | 5.5 (1-8) (30) | 3.5 (2-4) (20) | 2 (2-3) (7) |
| Other stray text reaching the founder (total) | 4 | 1 | 0 | 1 |
| Half-kept noop | 0 | 0 | 0 | 0 |
| Turns ended with exactly noop | 0 | 0 | 8 | 6 |
| Ended turns whose last block is text | 37/39 | 37/45 | 28/43 | 10/19 |
| Double reports | 9 | 6 | 5 | 3 |
| Builder closed its task without its word (e.g. "request #2") | 2 | 4 | 2 | 1 |
| hl say to the founder tried (refused) | 3 | 3 | 5 | 1 |
| Cost per run, $, median (min-max) | 0.034 (0.019-0.044) | 0.038 (0.010-0.067) | 0.033 (0.026-0.057) | 0.033 (0.030-0.050) |
| Output tokens per run, median (min-max) | 4534 (3345-7249) | 5062 (2971-10004) | 4088 (3019-6945) | 4282 (3751-6017) |
| Messages between agents (in Markdown) | 21 (0) | 24 (0) | 26 (0) | 10 (6) |

How the cells are counted (`count()` in the harness is the definition): a report is any agent's delivered text, or the
founder's task closed, with both words. An echo is delivered text, other than a report or a reply to the founder, written
within a minute of the same agent's `hl` command and naming another agent or a word. A double report is every report after
the first. A missed report is a run with no report, or a founder question whose code no delivered lead text carries.

Each run's raw events and its words are kept in `~/.handline/experiments/agent-talk-round2/<condition>-<n>/`
(`events.json`, `meta.json`); `python3 tools/agent_talk_round2.py --recount <that folder>` prints every row and this table
again. The counted rows are also in `docs/agent-talk-experiment.round2.jsonl`.

### What happened

- **The rule did nothing; noop helped a little.** Of the job's turns (started after the founder's task, and ended), 37 of
  39 ended in text in none, 37 of 45 in rule, 28 of 43 in noop (8 ended in `noop`, 7 in nothing). Echoes per run: 4 with no rule, 5.5 with the rule, 3.5 with noop; the
  ranges overlap. The echoes are turn summaries: "I read `secret.txt` and sent the word to talk.lead (queued as #5)", "Both
  builders have been asked ... I'm waiting on their replies". Haiku writes one at the end of almost every turn it does not
  end with `noop`, whatever the role says.
- **No half-kept noop was seen.** No agent wrote "noop. sent to talk.a"; it wrote either exactly `noop` or a full summary.
- **Replies to the founder worked, though not always at once.** All 10 mid-job questions were answered with the code in
  session text, 8 of them in the turn the question started; the other 2 came a turn or two later, in a turn a task notice
  started. The shim's refusal was tried 12 times in 21 runs before agents fell back to their session.
- **"Only a turn the founder started" is not enough either.** Delivering only those turns would have shown the founder 30
  text blocks in 21 runs, reports included, where all-text delivery shows 86 stray blocks besides the reports. But the
  report would not have arrived in 7 of 21 runs: there the lead wrote the words in a turn a task notice started and never
  closed the founder's task.
- **Reports double up.** The lead writes the words in its session and also closes the founder's task with them, so the
  founder gets both: 23 double reports in 21 runs. Builders' summaries also carry their word, so under all-text delivery
  the founder would see each word two or three times.
- **Numbers still stand in for content.** 9 builder closes carried no word, for example "reported the secret word to
  talk.lead as the answer to #4".

### Markdown through `--file -` (md)

7 of 10 `hl say` calls used `--file -`, and 6 of 10 bodies had Markdown, which in practice was backticks around the word.
Judged by whether a message was acted on with no question back and the run ended with both words: none of the 81
messages between agents (10 in md) drew a question, and every run succeeded, so Markdown changed nothing measurable. The messages in this job are a one-line request
and a one-word answer; a job with longer messages (a plan, a list of findings) would be the place to test it again.

## Message ids: hide them from agents, or make them short hashes (design note, no code change)

Today an agent sees each message's database row number in every delivery ("#N from talk.lead"), in `hl read <n>` and
`hl say <agent> --parent <n>`, and in notices like "T1 reported done by talk.b: request #2". The numbers only grow, look like
task numbers, and get used as content (the wordless closes above). Proposal, in order:

1. **Agents never need a message id.** A delivery ends with what to do, not a number: "reply with `hl say talk.lead`", where
   the reply threads to the newest open message from that sender; a digest is read with `hl inbox`. `--parent` and
   `hl read <n>` stay for people and the app.
2. **Where an agent must name one, it is a short handle.** For example `m-k3f9`: four base32 characters of an HMAC of the
   row id under a daemon key, in a unique column, lengthened on a collision. A handle does not grow or order and cannot be
   read as a task number or a count. `hl read`, `--parent` and every notice take and print the handle; the row id stays
   internal.
3. **Task numbers stay (T1, T2 ...).** People say them and they name the work. The task-board notice should quote the
   evidence, never a message number, and `hl task done` should refuse evidence that is only a message reference.

## Recommendation (for the founder to decide)

1. **Fix the structured launch first** (model and context), and rerun any agent experiment after it; until then no role
   instruction reaches an app-started agent.
2. **Do not deliver all session text to the founder, and do not rely on a prompt rule to make it safe.** With the best
   rule (noop), 3.5 echoes per run still reached the founder, and the plain rule changed nothing. The candidate to test
   next: deliver the text of a turn that a founder message started (8 of 10 replies), plus the founder's own task closed
   with its evidence, and tell agents exactly that ("to answer the founder, reply in the turn it wrote to you; to report,
   close its task"). Unprompted, that pair lost the report in 7 of 21 runs, so the instruction is what is under test.
   Everything else stays in the transcript, readable on demand.
3. **If session text is delivered, add noop anyway:** it is free (same cost) and cut text-ending turns from 95% to 65%;
   expect it to be slower (median 148 s against 98 s, ranges overlap).
4. **Hide message ids from agents** (design note above) and quote evidence, not numbers, in task notices.
5. **Markdown bodies:** allow them; no effect in this job.

## Round 1 (folded in; see the first section)

Round 1 ran 3 runs each of A (`hl say` and tasks), B (tasks only, `hl say` refused at its prompt) and N (as B, the lead
reports in its session), with `tools/agent_talk.py`; its rows are in `docs/agent-talk-experiment.runs.jsonl`. Because the
roles never reached the agents, the A/B/N columns differ only in whether `hl say` was refused. What still holds from it: 41
of 47 session text blocks were echoes of a send (the untold baseline round 2 starts from), a lead handed the founder's own
task down a chain instead of opening subtasks, and a lead closed the founder's task with "request #2" as the evidence.
