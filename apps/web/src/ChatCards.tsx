// What sits around the transcript of an agent's chat: the one-line strip of its state, the banner when it is stuck, and the HR proposals
// that concern it, decided where they were discussed.
import { useState } from 'react';
import { useSelector } from '@handline/app-react';
import type { AgentDetail } from '@handline/client';
import { useHr, useWrite } from '@handline/app-data';
import { Button, Card, CardWhat, Chip, Dialog, Field, Icon, State, stateOf } from './ui/index.ts';
import { goTeam } from './route.ts';
import { openTaskOnClick } from './ChatMessages.tsx';
import { useWorkspace } from './Workspace.tsx';

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
        <Chip
          as="a"
          href={`#/tasks/${detail.task.id}`}
          title={detail.task.title}
          onClick={openTaskOnClick(apply, detail.task.id)}
        >
          <Icon name="tasks" size={13} aria-hidden />T{detail.task.id}
        </Chip>
      ) : null}
      {cache !== null ? (
        <Chip
          title={cache > 0 ? `Prompt cache warm for ${cache} min` : 'Prompt cache has lapsed'}
          data-lapsed={cache <= 0 ? '' : undefined}
        >
          <Icon name={cache > 0 ? 'waiting' : 'unanswered'} size={13} aria-hidden />
          {cache > 0 ? `${cache}m` : null}
        </Chip>
      ) : null}
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

/** The open HR proposals that concern this agent, each with approve and reject as icons and a link to the Team page. */
export function HrCards({ agent }: { agent: string }) {
  const q = useHr({});
  const decide = useWrite('hr.decide');
  const can = useSelector((s) => s.can('hr.decide'));
  const [rejecting, setRejecting] = useState<{ id: number; subject: string } | null>(null);
  const [note, setNote] = useState('');
  const reject = () => {
    if (!rejecting || !note.trim()) return;
    decide.mutate({ proposal: rejecting.id, decision: 'reject', subject: rejecting.subject, note: note.trim() });
    setRejecting(null);
    setNote('');
  };
  const open = (q.data?.proposals ?? []).filter((p) => p.state === 'open' && (p.agent === agent || p.requested_by === agent));
  return (
    <>
      {open.map((p) => (
        <Card variant="chat" key={p.id} className="card-hr bg-surface" data-kind="hr" data-proposal={p.id}>
          <Icon name="team" label="Proposal" />
          <CardWhat stacked>
            <strong>{p.title}</strong>
            {p.reason ? <span className="muted">{p.reason}</span> : null}
          </CardWhat>
          {can ? (
            <>
              <Button
                icon="markRead"
                label="Approve"
                size="sm"
                onClick={() => decide.mutate({ proposal: p.id, decision: 'approve', subject: p.subject })}
              />
              <Button icon="dismiss" label="Reject" size="sm" onClick={() => setRejecting({ id: p.id, subject: p.subject })} />
            </>
          ) : null}
          <Button icon="open" label="Open in Team" size="sm" onClick={() => goTeam('hr')} />
        </Card>
      ))}
      {rejecting ? (
        <Dialog title="Reject" onClose={() => setRejecting(null)} action={{ label: 'Reject', onClick: reject, disabled: !note.trim() }}>
          <Field label="Why" value={note} onChange={(e) => setNote(e.target.value)} />
        </Dialog>
      ) : null}
    </>
  );
}
