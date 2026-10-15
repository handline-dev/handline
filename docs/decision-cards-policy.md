# Decision cards: what may interrupt, what waits

Research and proposal for T4444. Nothing is built; build only after the founder agrees, together with T4404. The founder's
complaint: approve/reject cards stay on screen, and there is no way to say "not now".

## 1. What other tools do

| Tool | What it shows | How you defer or clear it |
|---|---|---|
| Apple (iOS notifications) | Four interruption levels: passive (no alert, can wait), active (the default), time sensitive (breaks through Focus and the scheduled summary), critical (health and safety only). Time sensitive is for what "directly impacts the user and requires immediate attention" ([HIG, managing notifications](https://developer-rno.apple.com/design/human-interface-guidelines/patterns/managing-notifications), [WWDC21 10091](https://developer.apple.com/videos/play/wwdc2021/10091/)). | The system, not the app, decides whether a level may override Focus. |
| Linear inbox | One inbox for everything that needs you. | **Snooze** hides the item and brings it back unread at a chosen time (an hour, tomorrow, next week, or typed); new activity on the issue cancels the snooze. A **reminder** is separate and does not hide the item ([snooze](https://linear.app/docs/snooze), [changelog 2021-06-17](https://linear.app/changelog/2021-06-17-inbox-snooze-and-easier-issue-merge)). |
| GitHub notifications | An inbox at github.com/notifications, also mobile and email. | **Done** removes an item; **Save** keeps it flagged until you clear it; you can unsubscribe from a thread ([about notifications](https://docs.github.com/en/subscriptions-and-notifications/concepts/about-notifications)). |
| Slack | A "Remind me about this" on a message sends a notification at a time you pick and lists it under Later. | The weak point reported by a third party: the confirmation banner is gone in about three seconds, so the Later tab is the only way back ([Slack help](https://slack.com/intl/en-gb/help/articles/208423427-Set-a-reminder), [Zapier](https://zapier.com/blog/slack-reminders.md)). |
| Superhuman | Snooze is called Remind Me (shortcut H, plain-language time); the email returns if no reply came. | Only third-party pages were found, no current official help page ([shortcut list](https://www.pie-menu.com/shortcuts/superhuman)). |
| Interruption research | An interruption in the evaluation phase of a task costs more than at other points; an off-topic message costs more to handle and to resume from than a relevant one ([Cutrell et al., CHI 2000](https://www.interruptions.net/literature/Cutrell-CHI00.pdf)). Resumption lag is the cost measure; breakpoints in the task are cheaper moments ([Iqbal and Bailey, CHI 2006](https://interruptions.net/literature/Iqbal-CHI06-p741-iqbal.pdf)). Bounded deferral: hold an alert until the person is not busy, but no longer than a maximum wait ([Horvitz et al., 2005](https://www.interruptions.net/literature/Horvitz-UM05b.pdf)). | Not checked beyond abstracts; Gloria Mark's work did not come up. |

What repeats: one inbox is the home of everything pending; interruption is a rare, system-limited level; every item can be put
off to a time, and comes back by itself; new activity ends a snooze.

## 2. Proposed policy

**A. Three levels, and every decision starts at the lowest that fits.**

| Level | Means | Where it shows |
|---|---|---|
| Quiet | A decision that can wait: an HR proposal, a link, a context change, a merge ready for the founder. | The Inbox and the badge count only. No card in a chat, no sound, no notice. |
| Notice | A decision that blocks an agent the founder is likely watching: a permission or question the agent waits on. | The Inbox plus one system notice, once, and the chat's own banner for that agent only. |
| Interrupt | Money or safety: spend over a limit, a destructive action, a security fix waiting. | A notice that breaks through quiet hours, and the phone. Needs a reason in the code that raises it, as Apple limits time sensitive. |

HR proposals are Quiet. Today an open one is a card at the bottom of the agent's chat (and a notice on `hr.raised`); that is
the nagging.

**B. Open decision cards live in one place, not stacked in chats.** At most one open card for the agent on screen, and only
for a Notice-level decision that agent needs. Everything else is an Inbox row, with the same approve and reject buttons.

**C. Later, on every decision.** A third button next to approve and reject: *Later* with the Linear set (1 hour, tomorrow, next
week, a typed time). A snoozed item leaves the Inbox and the badge, and returns unread at the time. It returns early only if
something changes (the proposal changes, or an agent asks about it), as in Linear, never on a timer shorter than the one chosen.

**D. Expiry.** A Quiet decision with no answer is not deleted; after 14 days it is marked *stale* and moves to a Stale group
the founder can clear in one action. The agent that raised it is told once, so it can withdraw or ask again. A decision whose
subject is gone (the agent was removed, the task closed) closes itself. Numbers are a first guess to tune.

**E. Grouping.** Proposals of the same kind from the same agent in one hour become one row with a count ("3 context changes
from handline.pr"), with approve all and reject all, and each expandable. Grouping is by default in the Inbox, never in a chat.

**F. Decided means gone from the foreground.** An applied or rejected proposal is history: one line in the agent's timeline at
its time, never a card at the bottom (T4405 does this for the web chat).

**G. When one may interrupt.** Only at a Notice or Interrupt level, and then:
1. Not while the founder is typing in the compose box or has a dialog open; wait for the next pause (bounded deferral, at most
   a minute, then it goes to the Inbox with its notice).
2. At most one notice per agent per 10 minutes; further ones join the first.
3. Not for something the founder is looking at: no notice for an agent whose chat is on screen.
4. Quiet hours (set by the founder) hold Notice level until the end; Interrupt level goes through.
5. An agent cannot raise its own level. The daemon assigns it from the kind of decision.

## 3. Open for the founder

- Should an HR proposal ever be Notice (for example the first one after an agent is added)?
- Stale after 14 days, and the 10-minute notice limit: keep or change?
- Is *Later* needed on the phone as well? Recommendation: yes, the same set.

## 4. Where the scratch cards in the screenshot came from

The cards "link beta -> alpha", "link alpha -> beta" and "rename alpha" (reasons "scratch link reject", "scratch link
approve", "scratch proposal two") are not in the live HR list. They are in a scratch daemon home that the auditor's click-check
for the HR card tasks started:

- `/private/tmp/claude-501/h4418/state/hr/` holds 8 proposal files, including `4878d766.yaml` (link, beta, "scratch link
  reject", proposed), `64e44318.yaml` (link, alpha, "scratch link approve"), `948658f3.yaml` (rename, alpha, "scratch
  proposal two", proposed). Created 08:01 on 2026-10-16; the first decision was written 08:04.
- That folder also has the auditor's click scripts (`clickcard.js`, `c1.js` clicks the Approve of "scratch link approve") and a
  `daemon.log` of a daemon serving `/private/tmp/claude-501/h4418/state/daemon.sock`.
- An Electron build from the auditor's scratchpad (`.../handline-pc-auditor/.../scratchpad/wt-after`, `--remote-debugging-port=9333`)
  was running on this Mac at the time; the founder most likely saw that test window (inferred, not confirmed).
- The live store, `~/.handline/hr/` (159 files), has no file with those reasons: the only files containing "scratch" are
  ROLE.md context proposals, all approved. The fake daemon's source has no such proposals either.

So nothing is wrong with the live HR list; a click-check window looked like the real app. A test window should say it is one
(a title or a banner).
