# Copy: the words people read

Every string in the PC and phone apps is read by a busy person. The rules below apply to it, whether the app writes it or the
daemon sends it for the app to show.

1. **Say what happened, then what to do next.** "Held: the Mac is busy. It starts by itself when the load drops, or Start
   now." A string that names a problem and no next step is unfinished. When there is nothing to do, say that it fixes
   itself.
2. **One sentence where one works.** Cut the clause that repeats the title, the icon or the state word beside it.
3. **Plain words.** No hook names, state codes or internal nouns (`UserPromptSubmit`, `apply_failed`, `PARKED`, rpc, gate,
   tick, verb). Write numbers as people use them: "2.8 per core", not "load 2.84 per core, limit 1.50".
4. **No raw machinery in front of the person.** No file paths, ids (a task is "T612 · title", never an id alone), error
   codes, stack lines or command lines. A command goes behind a "Copy command" button, never into running text. A raw
   error from below is cut to its first sentence and goes behind "Details".
5. **No contradictions.** The headline and the detail must agree. Never "Not applied: the change is applied". If the
   detail comes from somewhere else, the app checks it fits the headline or leaves it out.
6. **No filler.** No "Please note", "Successfully", "Oops", "Something went wrong", "Are you sure?". Say what will
   happen instead: "Delete T612? Its comments go too."
7. **One word for one thing, everywhere.** Use these words and only these:

   | Word        | Means                                                                                           | Not                                      |
   | ----------- | ----------------------------------------------------------------------------------------------- | ---------------------------------------- |
   | working     | the agent is mid-turn                                                                           | running, active, busy                    |
   | needs you   | it waits for your answer; listed in **My turn**                                                 | waiting on you, needs, waiting for a yes |
   | held        | Handline holds it back on purpose (machine load, another agent's turn) and lets it go by itself | paused                                   |
   | waiting     | it waits on another task or a time it will reach by itself                                      | parked, sleeping, queued                 |
   | blocked     | it cannot go on until someone fixes something                                                   | stuck, stalled, failed                   |
   | idle        | nothing to do                                                                                   | finished its turn                        |
   | assigned to | the agent that does the task (the wire's `owner`)                                               | owner, owned by                          |
   | asked by    | who opened the task                                                                             | requester, creator                       |
   | owner       | who answers for the task, shown only when it is not the person who asked                        | the agent doing it                       |

   A person's action that failed is not a state: say "Couldn't <verb>: <why>" ("Couldn't start handline.pc: …"), or "Not
   applied: <why>" for a change.

8. **Buttons are verbs that say the result:** "Start now", "Approve", "Open terminal". Not "OK", "Submit", "Yes".

Reviewers and the auditor check each changed string against this page.
