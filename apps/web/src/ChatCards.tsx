// What sits around the transcript of an agent's chat: the one-line strip of its state, the banner when it is stuck, and the HR proposals
// that concern it, decided where they were discussed.
import { useState } from 'react';
import { useSelector } from '@handline/app-react';
import { HeldLine } from '#src/ApplyNow.tsx';
import type { AgentDetail, Proposal, TranscriptItem } from '@handline/client';
import {
  VERBS,
  type FollowUp,
  ago,
  clockTime,
  decidedProposals,
  applyFailure,
  followUpOf,
  isHeld,
  isWaiting,
  restartingLine,
  restartShown,
} from '@handline/app-core';
import { classify, sentence, useAgentStrip, useHr, useVerbMark, useVerbRun, useWorktrees, useWrite } from '@handline/app-data';
import { useServed } from '#src/AgentVerbs.tsx';
import { Button, Card, CardWhat, Chip, Dialog, Field, Icon } from '#src/ui/index.ts';
import { goTeam } from '#src/route.ts';
import { openTaskOnClick } from '#src/ChatMessages.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { AgentGauges, GaugeLine, CACHE_MINUTES } from '#src/AgentGauges.tsx';
import { AccountMenu, ModelMenu } from '#src/AgentPickers.tsx';
import { ViewSwitch } from '#src/ViewSwitch.tsx';
import { useOpenTerminal } from '#src/ChatMenus.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';
import { useNow } from '#src/Gauge.tsx';
import { BranchChip, ContextMax } from '#src/ChatStripItems.tsx';

const until = (iso: string | null, now: number): number | null => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : Math.round((t - now) / 60_000);
};

const tokens = (n: number): string => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));

/** One line under the composer: the model and account (menus), the task, the branch (dropped when the line is tight), and the context and
 *  prompt cache in words. */
export function ChatStrip({ detail, to = 'terminal' }: { detail: AgentDetail; to?: 'chat' | 'terminal' }) {
  const cache = until(detail.cache_until, useNow(30_000));
  const { apply } = useWorkspace();
  const strip = useAgentStrip(detail.name).data;
  const ctxPct = detail.ctx_pct ?? strip?.ctx_pct;
  const changed = useWorktrees({ owner: detail.name }).data?.worktrees.find((w) => w.path === detail.worktree?.path)?.changed.length;
  const files = `${changed} changed ${changed === 1 ? 'file' : 'files'} in the worktree`;
  const used = strip?.ctx_used;
  const limit = strip?.ctx_limit;
  return (
    <div className="chat-strip @container flex min-w-0 flex-1 items-center gap-1 overflow-hidden text-hint text-ink-2">
      <ModelMenu detail={detail} />
      <AccountMenu detail={detail} />
      {detail.task ? (
        <Chip
          as="a"
          href={`#/tasks/${detail.task.id}`}
          className="hover:bg-raised"
          title={detail.task.title}
          onClick={openTaskOnClick(apply, detail.task.id)}
        >
          <Icon name="tasks" size={13} aria-hidden />T{detail.task.id}
        </Chip>
      ) : null}
      {detail.worktree ? (
        <span className="branch hidden min-w-0 @[44rem]:inline-flex">
          <BranchChip branch={detail.worktree.branch} />
        </span>
      ) : null}
      {changed ? (
        <Chip title={files} aria-label={files}>
          <Icon name="changes" size={13} aria-hidden />
          {changed}
        </Chip>
      ) : null}
      <AgentGauges
        ctx={ctxPct == null ? null : Math.round(ctxPct)}
        cacheMin={cache}
        ctxText={used != null && limit != null ? `${tokens(used)} / ${tokens(limit)}` : undefined}
        cacheText={cache === null ? undefined : cache <= 0 ? 'lapsed' : `${cache}m`}
        ctxInfo={
          <>
            {used != null && limit != null ? (
              <GaugeLine what="Used">{`${used.toLocaleString()} / ${limit.toLocaleString()} tokens`}</GaugeLine>
            ) : null}
            {ctxPct != null ? <GaugeLine what="Full">{`${Math.round(ctxPct)}%`}</GaugeLine> : null}
            <ContextMax detail={detail} limit={limit} />
          </>
        }
        cacheInfo={
          cache === null ? null : (
            <>
              <GaugeLine what="Counts">time until the cache lapses</GaugeLine>
              <GaugeLine what={cache <= 0 ? 'Lapsed' : 'Left'}>
                {cache <= 0 ? ago(detail.cache_until!) : `${cache} of ${CACHE_MINUTES} min`}
              </GaugeLine>
              {cache > 0 ? <GaugeLine what="Lapses at">{clockTime(detail.cache_until!)}</GaugeLine> : null}
              <GaugeLine what="Reset">by the next turn, which warms it again</GaugeLine>
            </>
          )
        }
      />
      <ViewSwitch to={to} />
    </div>
  );
}

/** A banner with an icon and the reason when the session is blocked or stalled. */
export function ChatBanner({ detail }: { detail: AgentDetail }) {
  const why =
    detail.blocker || detail.wall_reason || (detail.state === 'stalled' ? detail.state_detail || 'The session stopped answering' : null);
  if (!why) return null;
  return (
    <Card variant="event" tone="bad" className="chat-banner" role="status">
      <Icon name="blocked" size={14} label="Blocked" />
      <span>{why}</span>
    </Card>
  );
}

/** The proposals that concern this agent. */
export const proposalsOf = (all: readonly Proposal[] | undefined, agent: string) =>
  (all ?? []).filter((p) => p.agent === agent || p.requested_by === agent);
/** The decided proposals of this agent, for the timeline. */
export const useDecided = (agent: string) => decidedProposals(useHr({}).data?.proposals, agent);

/** A decided proposal in the timeline: one compact line with the result. Only the person decides (hr.decide), so it was them. */
export function DecidedCard({ p }: { p: Proposal }) {
  const word =
    p.state === 'rejected' ? 'Rejected' : p.kind === 'add' && p.asked?.team ? `Applied: ${p.agent} added to ${p.asked.team}` : 'Applied';
  return (
    <Card
      variant="event"
      tone={p.state === 'rejected' ? 'none' : 'ok'}
      className="card-hr decided"
      data-kind="hr"
      data-proposal={p.id}
      data-state={p.state}
    >
      <Icon name="team" size={13} label="Proposal" />
      <span className="min-w-0 truncate">
        <strong className="font-medium text-ink">{p.title}</strong> · <span className="outcome">{word} by you</span> ·{' '}
        {p.settled ? clockTime(p.settled) : ''}
      </span>
      <Button icon="open" label="Open in Team" size="sm" onClick={() => goTeam('hr')} />
    </Card>
  );
}

/** An approved proposal whose change is applied but whose agent is not running: it says so, and Start now makes the start the daemon put off
 *  (or lost). Never "not applied", never approve again. */
function FollowUpCard({ p, follow }: { p: Proposal; follow: FollowUp }) {
  const run = useVerbRun(follow.agent);
  const busy = !!useVerbMark(follow.agent);
  const served = useServed();
  const [refused, setRefused] = useState<string | null>(null);
  const verb = VERBS.force_start;
  return (
    <Card
      variant="event"
      tone={follow.state === 'failed' ? 'bad' : 'wait'}
      className="card-hr"
      data-kind="hr"
      data-proposal={p.id}
      data-state={p.state}
      data-follow-up={follow.state}
    >
      <Icon name="team" label="Proposal" />
      <CardWhat stacked>
        <strong>{p.title}</strong>
        <span className="outcome">Applied</span>
        <span className="muted whitespace-normal">
          {follow.state === 'failed' ? follow.text : `${follow.agent} starts when the load drops`}
        </span>
        {refused ? <span role="alert">{refused}</span> : null}
      </CardWhat>
      {served(verb) ? (
        <Button
          label="Start now"
          size="sm"
          disabled={busy}
          onClick={() => (setRefused(null), run(verb, undefined, { onError: (e) => setRefused(sentence(classify(e))) }))}
        />
      ) : null}
      <Button icon="open" label="Open in Team" size="sm" onClick={() => goTeam('hr')} />
    </Card>
  );
}

/** The HR proposals that concern this agent and still wait: open ones with approve and reject as icons, a failed one with a retry. A decided one is
 *  a past event: it is in the timeline (DecidedCard), never down here. */
export function HrCards({ agent }: { agent: string }) {
  const q = useHr({});
  const decide = useWrite('hr.decide');
  const can = useSelector((s) => s.can('hr.decide'));
  const [rejecting, setRejecting] = useState<{ id: number; subject: string } | null>(null);
  const [note, setNote] = useState('');
  // The button pressed on a card, while it waits and after a refusal: the card says how it went.
  const [sent, setSent] = useState<Record<number, { choice: 'approve' | 'reject'; wait: boolean; error?: string }>>({});
  const send = (p: { id: number; subject: string }, choice: 'approve' | 'reject', why?: string) => {
    setSent((m) => ({ ...m, [p.id]: { choice, wait: true } }));
    decide.mutateAsync({ proposal: p.id, decision: choice, subject: p.subject, ...(why ? { note: why } : {}) }).then(
      () => setSent((m) => ({ ...m, [p.id]: { choice, wait: false } })),
      (e) => setSent((m) => ({ ...m, [p.id]: { choice, wait: false, error: sentence(classify(e)) } })),
    );
  };
  const reject = () => {
    if (!rejecting || !note.trim()) return;
    send(rejecting, 'reject', note.trim());
    setRejecting(null);
    setNote('');
  };
  const shown = proposalsOf(q.data?.proposals, agent).filter((p) => isWaiting(p) || isHeld(p) || followUpOf(p));
  return (
    <>
      {shown.map((p) => {
        const follow = followUpOf(p);
        if (follow) return <FollowUpCard key={p.id} p={p} follow={follow} />;
        const mine = sent[p.id];
        const failed = mine?.error; // a refusal of this press; the not-applied line is the card's own, shown once
        const apply = p.state === 'apply_failed' ? applyFailure(p) : null;
        const pressed = (c: 'approve' | 'reject') => !!mine && mine.choice === c;
        return (
          <Card
            variant="event"
            tone={p.state === 'apply_failed' ? 'bad' : 'wait'}
            key={p.id}
            className="card-hr"
            data-kind="hr"
            data-proposal={p.id}
            data-state={p.state}
          >
            <Icon name="team" label="Proposal" />
            <CardWhat stacked>
              <strong>{p.title}</strong>
              {p.state === 'apply_failed' ? (
                <>
                  <span className="outcome whitespace-normal text-bad">{apply!.line} Approve to try again.</span>
                  {apply!.details ? (
                    <details className="muted whitespace-normal">
                      <summary>Details</summary>
                      {apply!.details}
                    </details>
                  ) : null}
                </>
              ) : isHeld(p) ? (
                <HeldLine p={p} />
              ) : p.reason ? (
                <span className="muted">{p.reason}</span>
              ) : null}
              {failed ? (
                <span className="banner" role="status">
                  {failed}
                </span>
              ) : null}
            </CardWhat>
            {can && !isHeld(p) ? (
              <>
                <Button
                  icon="markRead"
                  label="Approve"
                  size="sm"
                  aria-pressed={pressed('approve')}
                  aria-busy={mine?.wait && pressed('approve') ? true : undefined}
                  disabled={!!mine && (mine.wait || !(failed || apply))}
                  className={pressed('approve') ? 'bg-raised' : ''}
                  onClick={() => send(p, 'approve')}
                >
                  {mine?.wait && pressed('approve') ? (
                    <span className="hr-wait" aria-hidden>
                      …
                    </span>
                  ) : undefined}
                </Button>
                <Button
                  icon="dismiss"
                  label="Reject"
                  size="sm"
                  aria-pressed={pressed('reject')}
                  aria-busy={mine?.wait && pressed('reject') ? true : undefined}
                  disabled={!!mine && (mine.wait || !(failed || apply))}
                  className={pressed('reject') ? 'bg-raised' : ''}
                  onClick={() => setRejecting({ id: p.id, subject: p.subject })}
                />
              </>
            ) : null}
            <Button icon="open" label="Open in Team" size="sm" onClick={() => goTeam('hr')} />
          </Card>
        );
      })}
      <AnimatedList>
        {rejecting ? (
          <Dialog
            key="reject"
            title="Reject"
            onClose={() => setRejecting(null)}
            action={{ label: 'Reject', onClick: reject, disabled: !note.trim() }}
          >
            <Field label="Why" value={note} onChange={(e) => setNote(e.target.value)} />
          </Dialog>
        ) : null}
      </AnimatedList>
    </>
  );
}

const NEEDS: Record<string, string> = {
  permission: 'Needs you',
  question: 'Asked you a question',
  usage: 'Out of usage',
  dialog: 'Waiting on a dialog',
};

/** What the agent waits on a person for, with how long and a way to its terminal; gone when the daemon stops saying it. */
export function NeedsYouCard({ detail }: { detail: AgentDetail }) {
  // A daemon that reads the screen sets has_dialog and serves no needs_you: the card still says it, with the daemon's own line.
  const need =
    detail.needs_you ??
    (detail.has_dialog ? { kind: 'dialog', since: detail.state_since, text: detail.state_detail || null, source: 'screen' } : null);
  const openTerminal = useOpenTerminal(detail);
  if (!need) return null;
  const reachable = detail.structured || !!detail.terminal;
  return (
    <Card variant="event" tone="wait" className="card-needs" data-kind="needs_you" data-need={need.kind}>
      <Icon name="blocked" label="Needs you" />
      <CardWhat stacked>
        <strong>{NEEDS[need.kind] ?? NEEDS.dialog}</strong>
        {need.text ? <span className="muted">{need.text}</span> : null}
        <span className="muted">{ago(need.since)}</span>
      </CardWhat>
      <Button icon="terminal" label="Open terminal" size="sm" disabled={!reachable} onClick={openTerminal} />
    </Card>
  );
}

/** While a session hands off: one centered line, its icon turning and its words changing in place, until the new session's line takes over. */
export function HandoffCard({ detail, items }: { detail: AgentDetail; items: TranscriptItem[] }) {
  const h = detail.handoff;
  if (!h || !restartShown(h, items)) return null;
  return (
    <div className="card-handoff flex items-center gap-3 text-meta text-primary" data-kind="handoff" data-phase={h.phase} role="status">
      <hr className="m-0 h-px flex-1 border-0 bg-line" />
      <span className="inline-flex items-center gap-1.5">
        <Icon name="refresh" size={14} aria-hidden className="animate-[ui-spin_1.2s_linear_infinite] motion-reduce:animate-none" />
        {restartingLine(h)}
      </span>
      <hr className="m-0 h-px flex-1 border-0 bg-line" />
    </div>
  );
}
