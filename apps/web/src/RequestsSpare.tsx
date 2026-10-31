// The weekly-loss offer inside its Requests item: the sentence and the first spare tasks, and Start and Dismiss as the item's answers.
import { SPARE_SHOWN, spareNotice, type Card, type SpareNotice } from '@handline/app-core';
import { useTasks } from '@handline/app-data';
import { Button as DsButton } from '#src/ds/Button.tsx';

/** The offer the item is, if its task is a weekly-loss one. */
export function useSpare(card: Card): SpareNotice | undefined {
  return (useTasks({}).data?.items ?? []).flatMap((t) => (t.id === card.task ? (spareNotice(t) ?? []) : []))[0];
}

/** The agent's own name: `handline.architect` is `architect`. */
const shortName = (agent: string) => agent.split('.').at(-1) ?? agent;

export function SpareAsk({ n }: { n: SpareNotice }) {
  const rows = new Map((useTasks({}).data?.items ?? []).map((t) => [t.id, t]));
  return (
    <div className="notice-spare flex flex-col gap-0.5">
      {n.ids.slice(0, SPARE_SHOWN).map((id) => (
        <span key={id} className="flex min-w-0 gap-1.5 text-xs text-ink-2">
          <b className="flex-none font-mono font-semibold text-ink">{`T${id}`}</b>
          <span className="min-w-0 flex-1 truncate">{rows.get(id)?.title ?? ''}</span>
          {rows.get(id) ? <span className="flex-none text-ink-3">{shortName(rows.get(id)!.to)}</span> : null}
        </span>
      ))}
      {n.ids.length > SPARE_SHOWN ? <span className="text-xs text-ink-3">{`and ${n.ids.length - SPARE_SHOWN} more`}</span> : null}
    </div>
  );
}

export function SpareAnswers({ n, onAnswer }: { n: SpareNotice; onAnswer: (evidence: 'start' | 'dismiss', line: string) => void }) {
  return (
    <>
      <DsButton
        kind="primary"
        label={n.ids.length === 1 ? 'Start 1 task' : `Start ${n.ids.length} tasks`}
        disabled={!n.ids.length}
        onClick={() => onAnswer('start', 'Started')}
      />
      <DsButton label="Dismiss" onClick={() => onAnswer('dismiss', 'Dismissed')} />
    </>
  );
}
