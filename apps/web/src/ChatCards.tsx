// What sits around the transcript of an agent's chat: the one-line strip of its state, the banner when it is stuck, and the HR proposals
// that concern it, decided where they were discussed.
import { useState } from 'react';
import { useSelector } from '@handline/app-react';
import type { AgentDetail, Proposal } from '@handline/client';
import { ago } from '@handline/app-core';
import { classify, sentence, useHr, useWrite } from '@handline/app-data';
import { Button, Card, CardWhat, Chip, Dialog, Field, Icon, State, stateOf } from './ui/index.ts';
import { goTeam } from './route.ts';
import { openTaskOnClick } from './ChatMessages.tsx';
import { useWorkspace } from './Workspace.tsx';
import { AgentGauges } from './AgentGauges.tsx';
import { useOpenTerminal } from './ChatMenus.tsx';

const until = (iso: string | null): number | null => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : Math.round((t - Date.now()) / 60_000);
};

/** One line of icons: the state, the model and effort, the account, the task, and the prompt cache's clock. The words are tooltips. */
export function ChatStrip({ detail }: { detail: AgentDetail }) {
  const cache = until(detail.cache_until);
  const { apply } = useWorkspace();
  return (
    <div className="chat-strip flex min-w-0 items-center gap-2 overflow-hidden text-hint text-ink-2">
      <State kind={stateOf(detail.state)} label={detail.state_detail ? `${detail.state}: ${detail.state_detail}` : detail.state} />
      {detail.model ? (
        <Chip title={`Model ${detail.model}${detail.effort ? `, ${detail.effort}` : ''}`}>
          <Icon name="agent" size={13} aria-hidden />
          {detail.model}
          {detail.effort ? <span className="muted"> · {detail.effort}</span> : null}
        </Chip>
      ) : null}
      {detail.account ? (
        <Chip title={`Account ${detail.account}`}>
          <Icon name="accounts" size={13} aria-hidden />
          {detail.account}
        </Chip>
      ) : null}
      {detail.task ? (
        <Chip as="a" href={`#/tasks/${detail.task.id}`} title={detail.task.title} onClick={openTaskOnClick(apply, detail.task.id)}>
          <Icon name="tasks" size={13} aria-hidden />T{detail.task.id}
        </Chip>
      ) : null}
      <AgentGauges ctx={typeof detail.ctx_pct === 'number' ? Math.round(detail.ctx_pct) : null} cacheMin={cache} />
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

const DONE: Record<string, string> = { approved: 'Applied', rejected: 'Rejected' };
/** What the card says once a decision came back: what was done, or why it was not. */
function outcome(p: Proposal, choice: 'approve' | 'reject', error?: string): string {
  if (error) return error;
  if (choice === 'reject') return 'Rejected';
  return p.kind === 'add' && p.asked?.team ? `${p.agent} added to ${p.asked.team}` : 'Applied';
}
const settledRecently = (iso?: string | null) => !!iso && Date.now() - Date.parse(iso) < 60 * 60_000;

/** The HR proposals that concern this agent: open ones with approve and reject as icons, a failed one with a retry, and a decided one as the same
 *  card showing the outcome for an hour. */
export function HrCards({ agent }: { agent: string }) {
  const q = useHr({});
  const decide = useWrite('hr.decide');
  const can = useSelector((s) => s.can('hr.decide'));
  const [rejecting, setRejecting] = useState<{ id: number; subject: string } | null>(null);
  const [note, setNote] = useState('');
  // The button pressed on a card, while it waits and after: the card stays and says how it went (the app posts nothing in the chat).
  const [sent, setSent] = useState<Record<number, { choice: 'approve' | 'reject'; wait: boolean; error?: string }>>({});
  const send = (p: { id: number; subject: string }, choice: 'approve' | 'reject', why?: string) => {
    setSent((m) => ({ ...m, [p.id]: { choice, wait: true } }));
    decide
      .mutateAsync({ proposal: p.id, decision: choice, subject: p.subject, ...(why ? { note: why } : {}) })
      .then(
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
  const mine = (q.data?.proposals ?? []).filter((p) => p.agent === agent || p.requested_by === agent);
  const shown = mine.filter((p) => p.state === 'open' || p.state === 'apply_failed' || (p.state in DONE && settledRecently(p.settled)));
  return (
    <>
      {shown.map((p) => {
        const mine = sent[p.id];
        const failed = mine?.error ?? (mine && !mine.wait && p.state === 'apply_failed' ? `Not applied${p.error ? `: ${p.error}` : ''}` : undefined);
        const pressed = (c: 'approve' | 'reject') => !!mine && mine.choice === c;
        return (
          <Card variant="chat" key={p.id} className="card-hr bg-surface" data-kind="hr" data-proposal={p.id} data-state={p.state}>
            <Icon name="team" label="Proposal" />
            <CardWhat stacked>
              <strong>{p.title}</strong>
              {p.state in DONE ? (
                <>
                  <span className="outcome text-ink-2">{DONE[p.state]}</span>
                  <span className="muted whitespace-pre-line">{p.diff}</span>
                </>
              ) : p.state === 'apply_failed' ? (
                <span className="outcome text-bad">Not applied{p.error ? `: ${p.error}` : ''}. Approve to try again.</span>
              ) : p.reason ? (
                <span className="muted">{p.reason}</span>
              ) : null}
              {mine && !mine.wait ? (
                <span className={`banner${failed ? '' : ' ok'}`} role="status">
                  {failed ?? outcome(p, mine.choice)}
                </span>
              ) : null}
            </CardWhat>
            {can && (!(p.state in DONE) || mine) ? (
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
                  {mine?.wait && pressed('approve') ? <span className="hr-wait" aria-hidden>…</span> : undefined}
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
      {rejecting ? (
        <Dialog title="Reject" onClose={() => setRejecting(null)} action={{ label: 'Reject', onClick: reject, disabled: !note.trim() }}>
          <Field label="Why" value={note} onChange={(e) => setNote(e.target.value)} />
        </Dialog>
      ) : null}
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
