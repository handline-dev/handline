// The chat's cards for why an agent is not working: held for the machine's load (a button starts it now), blocked by another task, or
// waiting on you. The sidebar row says the same line; here it is a card that goes when the cause does.
import { useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { ago, openTask, taskRef, VERBS } from '@handline/app-core';
import { classify, sentence, useTaskTitles, useVerbMark, useVerbRun } from '@handline/app-data';
import { useServed } from '#src/AgentVerbs.tsx';
import { openTaskOnClick } from '#src/ChatMessages.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { Button, Card, CardWhat, Icon } from '#src/ui/index.ts';

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Shown while the daemon says the agent waits on other tasks or on you; gone when the cause closes. A dialog the agent has open is the needs-you card's. */
export function BlockedCard({ detail }: { detail: AgentDetail }) {
  const { apply } = useWorkspace();
  const status = detail.status;
  const ids = status?.reason === 'task' ? status.tasks : [];
  const titles = useTaskTitles(ids);
  if (!status) return null;
  const you = status.reason === 'you' && !detail.needs_you && !detail.has_dialog;
  if (status.reason !== 'task' && !you) return null;
  return (
    <Card variant="event" tone="wait" className="card-blocked" data-kind="blocked" data-reason={status.reason}>
      <Icon name="blocked" label="Blocked" />
      <CardWhat stacked>
        <strong className="whitespace-normal">{capital(status.text || (you ? 'Needs you' : 'Blocked'))}</strong>
        {ids.map((id) => (
          <a key={id} className="muted underline" href={`#/tasks/${id}`} onClick={openTaskOnClick(apply, id)}>
            {taskRef(id)}
            {titles.get(id) ? ` ${titles.get(id)}` : ''}
          </a>
        ))}
        {status.since ? <span className="muted">for {ago(status.since)}</span> : null}
      </CardWhat>
      {you && detail.task ? <Button label="Open" size="sm" onClick={() => apply((w) => openTask(w, detail.task!.id))} /> : null}
    </Card>
  );
}

/** Shown only while the daemon says the agent waits on the load limit; gone when the work starts. */
export function HeldCard({ detail }: { detail: AgentDetail }) {
  const run = useVerbRun(detail.name);
  const busy = !!useVerbMark(detail.name);
  const served = useServed();
  const [refused, setRefused] = useState<string | null>(null);
  const status = detail.status;
  if (status?.reason !== 'load' && status?.reason !== 'pace') return null;
  const verb = VERBS.force_start;
  return (
    <Card variant="event" tone="wait" className="card-held" data-kind="held" data-reason={status.reason}>
      <Icon name="blocked" label="Held" />
      <CardWhat stacked>
        <strong className="whitespace-normal">{status.text || 'Held: the Mac is busy. It starts by itself when that drops.'}</strong>
        {status.since ? <span className="muted">for {ago(status.since)}</span> : null}
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
    </Card>
  );
}
