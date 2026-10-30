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
import { Chip, Icon } from '#src/ui/index.ts';
import { Button } from '#src/ds/Button.tsx';
import { ExternalLink, Terminal } from 'lucide-react';
import { Notice } from '#src/ds/Chat.tsx';
import { goTeam } from '#src/route.ts';
import { openTaskOnClick } from '#src/ChatMessages.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { AgentGauges, GaugeLine, CACHE_MINUTES } from '#src/AgentGauges.tsx';
import { AccountMenu, ModelMenu } from '#src/AgentPickers.tsx';
import { ViewSwitch } from '#src/ViewSwitch.tsx';
import { useOpenTerminal } from '#src/ChatMenus.tsx';
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
    <div className="chat-strip @container flex min-w-0 flex-1 items-center gap-1 overflow-hidden text-xs text-ink-2">
      <ModelMenu detail={detail} />
      <AccountMenu detail={detail} />
      {detail.task ? (
        <Chip
          as="a"
          href={`#/tasks/${detail.task.id}`}
          className="tint"
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

/** The grey line that stays when a daemon stall is solved, by the title it had. */
const SOLVED: Record<string, string> = {
  'Waiting for folder trust': 'Folder trust answered',
  'Stuck at a start-up dialog': 'Start-up dialog answered',
  'Stuck at a dialog': 'Dialog answered',
  "Won't start": 'Started again',
  "Woke but didn't start": 'Started',
};

/** What the agent waits on a person for: the title, the sentence under it and one button (the terminal, or Try again for a start that failed); gone when the daemon stops saying it, leaving one grey line for a stall it solved. */
export function NeedsYouCard({ detail }: { detail: AgentDetail }) {
  // A daemon that reads the screen sets has_dialog and serves no needs_you: the card still says it, with the daemon's own line.
  const need =
    detail.needs_you ??
    (detail.has_dialog
      ? { kind: 'dialog', since: detail.state_since, title: 'Waiting at a dialog', text: detail.state_detail || null, source: 'screen' }
      : null);
  const openTerminal = useOpenTerminal(detail);
  const run = useVerbRun(detail.name);
  const now = useNow();
  const [solved, setSolved] = useState<{ agent: string; title: string; at: string } | null>(null);
  const [seen, setSeen] = useState<{ agent: string; title: string } | null>(null);
  const title = need?.source === 'daemon' && SOLVED[need.title] ? need.title : null;
  if (title && (seen?.agent !== detail.name || seen.title !== title)) setSeen({ agent: detail.name, title });
  if (title && solved) setSolved(null);
  if (!title && seen) {
    if (seen.agent === detail.name) setSolved({ ...seen, at: new Date().toISOString() });
    setSeen(null);
  }
  if (!need) {
    return solved?.agent === detail.name ? (
      <div className="card-solved text-meta text-ink-2" data-kind="solved" role="status">
        {SOLVED[solved.title]} · {ago(solved.at, now)}
      </div>
    ) : null;
  }
  const reachable = detail.structured || !!detail.terminal;
  const again = need.kind === 'start_failed' || need.kind === 'no_session';
  return (
    <Notice
      icon={<Icon name="blocked" label="Needs you" />}
      className="card-needs"
      data-kind="needs_you"
      data-need={need.kind}
      actions={
        again ? (
          <Button size="s" label="Try again" onClick={() => run(VERBS.clear_stall)} />
        ) : (
          <Button size="s" icon={Terminal} label="Open the terminal" disabled={!reachable} onClick={openTerminal} />
        )
      }
    >
      <strong>{need.title}</strong>
      <span className="muted"> · {ago(need.since)}</span>
      {need.text ? <span className="muted block">{need.text}</span> : null}
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
