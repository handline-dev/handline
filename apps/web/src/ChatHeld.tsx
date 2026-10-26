// The chat's line for an agent the daemon holds back for the machine's load, with a button that starts its work now.
import { useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { ago, VERBS } from '@handline/app-core';
import { classify, sentence, useVerbMark, useVerbRun } from '@handline/app-data';
import { useServed } from '#src/AgentVerbs.tsx';
import { Button, Card, CardWhat, Icon } from '#src/ui/index.ts';

/** Shown only while the daemon says the agent waits on the load limit; gone when the work starts. */
export function HeldCard({ detail }: { detail: AgentDetail }) {
  const run = useVerbRun(detail.name);
  const busy = !!useVerbMark(detail.name);
  const served = useServed();
  const [refused, setRefused] = useState<string | null>(null);
  const status = detail.status;
  if (status?.reason !== 'load') return null;
  const verb = VERBS.force_start;
  return (
    <Card variant="chat" className="card-held bg-surface" data-kind="held" data-reason="load">
      <Icon name="blocked" label="Held" />
      <CardWhat stacked>
        <strong>{status.text || 'Held: machine load'}</strong>
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
