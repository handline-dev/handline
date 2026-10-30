// What sits around the transcript of an agent's chat: the one-line strip of its state, the banner when it is stuck, and the HR proposals
// that concern it, decided where they were discussed.
import { useState } from 'react';
import type { AgentDetail, Proposal, TranscriptItem } from '@handline/client';
import {
  VERBS,
  type FollowUp,
  ago,
  clockTime,
  decidedProposals,
  followUpOf,
  isWaiting,
  restartingLine,
  restartShown,
} from '@handline/app-core';
import { classify, sentence, useAgentStrip, useHr, useVerbMark, useVerbRun, useWorktrees } from '@handline/app-data';
import { useServed } from '#src/AgentVerbs.tsx';
import { Icon } from '#src/ui/index.ts';
import { Button } from '#src/ds/Button.tsx';
import { ExternalLink, Terminal } from 'lucide-react';
import { Notice } from '#src/ds/Chat.tsx';
import { goTeam } from '#src/route.ts';
import { AgentGauges, GaugeLine } from '#src/AgentGauges.tsx';
import { AccountMenu, ModelMenu } from '#src/AgentPickers.tsx';
import { ViewSwitch } from '#src/ViewSwitch.tsx';
import { useOpenTerminal } from '#src/ChatMenus.tsx';
import { useNow } from '#src/Gauge.tsx';
import { BranchMenu, ContextBar, ContextMax, StartFresh } from '#src/ChatStripItems.tsx';

const until = (iso: string | null, now: number): number | null => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : Math.round((t - now) / 60_000);
};

/** One line under the composer: the model and account (menus), the task, the branch (dropped when the line is tight), and the context and
 *  prompt cache in words. */
export function ChatStrip({ detail, to = 'terminal' }: { detail: AgentDetail; to?: 'chat' | 'terminal' }) {
  const cache = until(detail.cache_until, useNow(30_000));
  const strip = useAgentStrip(detail.name).data;
  const ctxPct = detail.ctx_pct ?? strip?.ctx_pct;
  const changed = useWorktrees({ owner: detail.name }).data?.worktrees.find((w) => w.path === detail.worktree?.path)?.changed ?? [];
  const used = strip?.ctx_used;
  const limit = strip?.ctx_limit;
  return (
    <div className="chat-strip @container flex min-w-0 flex-1 items-center gap-1 overflow-hidden text-xs text-ink-2">
      <AgentGauges
        ctx={ctxPct == null ? null : Math.round(ctxPct)}
        cacheMin={null}
        ctxText={ctxPct == null ? undefined : `${Math.round(ctxPct)}% context`}
        ctxInfo={
          <>
            {used != null ? <ContextBar detail={detail} used={used} limit={limit} /> : null}
            {cache !== null && cache > 0 ? <GaugeLine what="Prompt cache">{`lapses in ${cache}m`}</GaugeLine> : null}
            <ContextMax detail={detail} />
            <StartFresh detail={detail} />
          </>
        }
      />
      {detail.worktree ? (
        <span className="branch hidden min-w-0 @[44rem]:inline-flex">
          <BranchMenu branch={detail.worktree.branch} changed={changed} />
        </span>
      ) : null}
      <AccountMenu detail={detail} />
      <ModelMenu detail={detail} />
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
    <Notice icon={<Icon name="blocked" size={14} label="Blocked" />} className="chat-banner" role="status">
      {why}
    </Notice>
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
    <Notice
      icon={<Icon name="team" size={13} label="Proposal" />}
      className="card-hr decided"
      data-kind="hr"
      data-proposal={p.id}
      data-state={p.state}
      actions={<Button iconOnly size="s" icon={ExternalLink} label="Open in Team" onClick={() => goTeam('hr')} />}
    >
      <strong className="font-medium text-ink">{p.title}</strong> · <span className="outcome">{word} by you</span> ·{' '}
      {p.settled ? clockTime(p.settled) : ''}
    </Notice>
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
    <Notice
      icon={<Icon name="team" label="Proposal" />}
      className="card-hr"
      data-kind="hr"
      data-proposal={p.id}
      data-state={p.state}
      data-follow-up={follow.state}
      actions={
        served(verb) ? (
          <Button
            label="Start now"
            size="s"
            disabled={busy}
            onClick={() => (setRefused(null), run(verb, undefined, { onError: (e) => setRefused(sentence(classify(e))) }))}
          />
        ) : (
          <Button iconOnly size="s" icon={ExternalLink} label="Open in Team" onClick={() => goTeam('hr')} />
        )
      }
    >
      <strong>{p.title}</strong>
      {' · '}
      <span className="outcome">Applied</span>
      {` · ${follow.state === 'failed' ? follow.text : `${follow.agent} starts when the load drops`}`}
      {refused ? <span role="alert"> · {refused}</span> : null}
    </Notice>
  );
}

/** The HR proposals that concern this agent and still wait are decided in My turn and in Team > HR; the chat says how many in one line. A decided
 *  one is a past event: it is in the timeline (DecidedCard). */
export function HrCards({ agent }: { agent: string }) {
  const q = useHr({});
  const all = proposalsOf(q.data?.proposals, agent).filter((p) => isWaiting(p) || followUpOf(p));
  const follows = all.flatMap((p) => {
    const follow = followUpOf(p);
    return follow ? [{ p, follow }] : [];
  });
  const open = all.length - follows.length;
  return (
    <>
      {follows.map(({ p, follow }) => (
        <FollowUpCard key={p.id} p={p} follow={follow} />
      ))}
      {open ? (
        <Notice
          icon={<Icon name="team" label="Proposal" />}
          className="card-hr"
          data-kind="hr"
          actions={<Button iconOnly size="s" icon={ExternalLink} label="Open in HR" onClick={() => goTeam('hr')} />}
        >
          HR proposals: {open} · needs you
        </Notice>
      ) : null}
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
    <Notice
      icon={<Icon name="blocked" label="Needs you" />}
      className="card-needs"
      data-kind="needs_you"
      data-need={need.kind}
      actions={<Button iconOnly size="s" icon={Terminal} label="Open terminal" disabled={!reachable} onClick={openTerminal} />}
    >
      <strong>{NEEDS[need.kind] ?? NEEDS.dialog}</strong>
      {need.text ? <span className="muted"> · {need.text}</span> : null}
      <span className="muted"> · {ago(need.since)}</span>
    </Notice>
  );
}

/** While a session hands off: one centered line, its icon turning and its words changing in place, until the new session's line takes over. */
export function HandoffCard({ detail, items }: { detail: AgentDetail; items: TranscriptItem[] }) {
  const h = detail.handoff;
  if (!h || !restartShown(h, items)) return null;
  return (
    <div className="card-handoff flex items-center gap-3 text-meta text-acc" data-kind="handoff" data-phase={h.phase} role="status">
      <hr className="m-0 h-px flex-1 border-0 bg-line" />
      <span className="inline-flex items-center gap-1.5">
        <Icon name="refresh" size={14} aria-hidden className="animate-spin motion-reduce:animate-none" />
        {restartingLine(h)}
      </span>
      <hr className="m-0 h-px flex-1 border-0 bg-line" />
    </div>
  );
}
