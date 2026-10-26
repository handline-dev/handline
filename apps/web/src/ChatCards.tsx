// What sits around the transcript of an agent's chat: the one-line strip of its state, the banner when it is stuck, and the HR proposals
// that concern it, decided where they were discussed.
import { useEffect, useState } from 'react';
import { useSelector } from '@handline/app-react';
import type { AgentDetail, Proposal, TranscriptItem } from '@handline/client';
import { ago, clockTime, decidedProposals, heldLine, isHeld, isWaiting, restartingLine, restartShown } from '@handline/app-core';
import { classify, sentence, useAgentStrip, useHr, useWorktrees, useWrite } from '@handline/app-data';
import { Button, Card, CardWhat, Chip, Dialog, Field, Icon } from '#src/ui/index.ts';
import { goTeam } from '#src/route.ts';
import { openTaskOnClick } from '#src/ChatMessages.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { AgentGauges, GaugeLine, CACHE_MINUTES } from '#src/AgentGauges.tsx';
import { AccountMenu, ModelMenu } from '#src/AgentPickers.tsx';
import { ViewSwitch } from '#src/ViewSwitch.tsx';
import { useOpenTerminal } from '#src/ChatMenus.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';

const until = (iso: string | null): number | null => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : Math.round((t - Date.now()) / 60_000);
};

const tokens = (n: number): string => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));

/** One line under the composer: the model and account (menus), the task, the branch (dropped when the line is tight), and the context and
 *  prompt cache in words. */
export function ChatStrip({ detail }: { detail: AgentDetail }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);
  const cache = until(detail.cache_until);
  const { apply } = useWorkspace();
  const strip = useAgentStrip(detail.name).data;
  const ctxPct = detail.ctx_pct ?? strip?.ctx_pct;
  const changed = useWorktrees({ owner: detail.name }).data?.worktrees.find((w) => w.path === detail.worktree?.path)?.changed.length;
  const files = `${changed} changed ${changed === 1 ? 'file' : 'files'} in the worktree`;
  const used = strip?.ctx_used;
  const limit = strip?.ctx_limit;
  return (
    <div className="chat-strip @container flex min-w-0 flex-1 items-center gap-2 overflow-hidden text-hint text-ink-2">
      <ModelMenu detail={detail} />
      <AccountMenu detail={detail} />
      {detail.task ? (
        <Chip as="a" href={`#/tasks/${detail.task.id}`} title={detail.task.title} onClick={openTaskOnClick(apply, detail.task.id)}>
          <Icon name="tasks" size={13} aria-hidden />T{detail.task.id}
        </Chip>
      ) : null}
      {detail.worktree ? (
        <span className="branch hidden min-w-0 @[44rem]:inline-flex">
          <Chip className="min-w-0" title={`Branch ${detail.worktree.branch}`}>
            <Icon name="worktrees" size={13} aria-hidden />
            <span className="truncate">{detail.worktree.branch}</span>
          </Chip>
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
      <ViewSwitch to="terminal" />
    </div>
  );
}

/** A banner with an icon and the reason when the session is blocked or stalled. */
export function ChatBanner({ detail }: { detail: AgentDetail }) {
  const why =
    detail.blocker || detail.wall_reason || (detail.state === 'stalled' ? detail.state_detail || 'The session has stalled' : null);
  if (!why) return null;
  return (
    <div className="chat-banner flex items-center gap-2 border-l-[3px] border-l-bad bg-panel px-2 py-1.5 text-body" role="status">
      <Icon name="blocked" size={14} label="Blocked" />
      <span>{why}</span>
    </div>
  );
}

/** The room's name over the chat, with the agent's state. */
export const RoomTitle = ({ agent, detail }: { agent: string; detail: AgentDetail }) => (
  <h2 className="m-0 min-w-0 flex-1 truncate text-body font-medium">
    {agent} <span className="muted text-hint font-normal">{detail.status?.text ?? (detail.handoff ? 'Restarting…' : detail.state)}</span>
  </h2>
);

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
    <div className="card-hr decided flex items-center gap-2 text-hint text-ink-2" data-kind="hr" data-proposal={p.id} data-state={p.state}>
      <Icon name="team" size={13} label="Proposal" />
      <span className="min-w-0 truncate">
        <strong className="font-medium text-ink">{p.title}</strong> · <span className="outcome">{word} by you</span> ·{' '}
        {p.settled ? clockTime(p.settled) : ''}
      </span>
      <Button icon="open" label="Open in Team" size="sm" onClick={() => goTeam('hr')} />
    </div>
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
  const shown = proposalsOf(q.data?.proposals, agent).filter((p) => isWaiting(p) || isHeld(p));
  return (
    <>
      {shown.map((p) => {
        const mine = sent[p.id];
        const failed =
          mine?.error ?? (mine && !mine.wait && p.state === 'apply_failed' ? `Not applied${p.error ? `: ${p.error}` : ''}` : undefined);
        const pressed = (c: 'approve' | 'reject') => !!mine && mine.choice === c;
        return (
          <Card variant="chat" key={p.id} className="card-hr bg-surface" data-kind="hr" data-proposal={p.id} data-state={p.state}>
            <Icon name="team" label="Proposal" />
            <CardWhat stacked>
              <strong>{p.title}</strong>
              {p.state === 'apply_failed' ? (
                <span className="outcome text-bad">Not applied{p.error ? `: ${p.error}` : ''}. Approve to try again.</span>
              ) : isHeld(p) ? (
                <span className="muted hr-held">{heldLine(p)}</span>
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
                  disabled={!!mine && (mine.wait || !failed)}
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
                  disabled={!!mine && (mine.wait || !failed)}
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
  permission: 'Waiting for a yes',
  question: 'Asked you a question',
  usage: 'Hit the session limit',
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
    <Card variant="chat" className="card-needs bg-surface" data-kind="needs_you" data-need={need.kind}>
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
