// A proposal an agent just raised, shown on whatever page the founder is on with Approve and Reject (a reason is needed), as the bridge's
// HR dot and request put it in front of them. Not drawn on the HR tab, which already lists every card, nor for anyone but the founder.
import { useState } from 'react';
import { raised, textSafe } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { classify, sentence, useHr, useWrite } from '@handline/app-data';
import type { Proposal } from '@handline/client';
import { Button, Icon } from './ui/index.ts';
import { goTeam, useTeamRoute } from './route.ts';
import { useNarrow } from './useNarrow.ts';

const SHOWN = 3;

export function HrBanner() {
  const can = useSelector((s) => s.can('hr.decide'));
  const narrow = useNarrow();
  const onHr = useTeamRoute()?.tab === 'hr';
  const list = useHr().data?.proposals ?? [];
  if (!can || onHr) return null;
  const { shown, more } = raised(list, SHOWN);
  if (!shown.length) return null;
  return (
    <aside className="hr-banner fixed top-3 right-4 z-40 flex w-[min(420px,calc(100vw-2rem))] flex-col gap-2" aria-label="New HR proposals">
      {shown.map((p) => (
        <Item key={p.id} p={p} readOnly={narrow} />
      ))}
      {more ? <Button variant="outline" size="sm" label={`${more} more in HR`} onClick={() => goTeam('hr')} /> : null}
    </aside>
  );
}

function Item({ p, readOnly }: { p: Proposal; readOnly: boolean }) {
  const decide = useWrite('hr.decide', String(p.id));
  const [reason, setReason] = useState<string | null>(null);
  const [said, setSaid] = useState('');
  const send = (call: Promise<unknown>) => {
    setSaid('');
    call.catch((e) => setSaid(sentence(classify(e))));
  };
  const busy = decide.isPending;
  const why = reason?.trim() ?? '';
  return (
    <section
      className="hr-banner-item flex flex-col gap-1.5 rounded-card border border-line bg-surface p-3 shadow-[var(--shadow-pop)]"
      data-proposal={p.id}
    >
      <strong className="flex items-center gap-1.5">
        <Icon name="team" size={16} aria-hidden="true" /> {textSafe(p.title)}
      </strong>
      {p.requested_by ? <span className="muted">from {textSafe(p.requested_by)}</span> : null}
      {p.reason ? <span>{textSafe(p.reason)}</span> : null}
      {said ? (
        <span className="text-bad" role="status">
          {said}
        </span>
      ) : null}
      {readOnly ? (
        <div className="card-actions flex gap-2">
          <Button variant="ghost" icon="open" label="Review" onClick={() => goTeam('hr')} />
        </div>
      ) : reason === null ? (
        <div className="card-actions flex gap-2">
          <Button
            variant="solid"
            icon="markRead"
            label="Approve"
            disabled={busy}
            onClick={() => send(decide.mutateAsync({ proposal: p.id, decision: 'approve', subject: p.subject }))}
          />
          <Button variant="outline" icon="close" label="Reject" disabled={busy} onClick={() => setReason('')} />
          <Button variant="ghost" icon="open" label="Review" onClick={() => goTeam('hr')} />
        </div>
      ) : (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!why || busy) return;
            setReason(null);
            send(decide.mutateAsync({ proposal: p.id, decision: 'reject', note: why, subject: p.subject }));
          }}
        >
          <input
            type="text"
            className="compose flex-1"
            autoFocus
            aria-label="Why reject"
            placeholder="why - Enter rejects, Esc cancels"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setReason(null)}
          />
        </form>
      )}
    </section>
  );
}
