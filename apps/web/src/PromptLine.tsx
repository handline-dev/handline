// A prompt the agent asked, as one quiet line: what it asked, and once answered what it got and when.
import type { TranscriptItem } from '@handline/client';
import { ago, clockTime } from '@handline/app-core';
import { CardCode, Icon } from '#src/ui/index.ts';
import { Notice } from '#src/ds/Chat.tsx';

export function PromptLine({ i }: { i: TranscriptItem }) {
  const p = i.prompt;
  const name = p?.kind === 'permission' ? 'permission' : 'prompt';
  const ans = i.answer && 'label' in i.answer ? i.answer : null;
  const what = p?.kind === 'permission' ? `Asked permission: ${p.tool ?? ''}` : `Asked: ${p?.kind ?? 'a dialog'}`;
  return (
    <Notice
      icon={<Icon name={name} label={what} />}
      className="card-prompt group w-fit max-w-full"
      data-kind="prompt"
      data-open={i.open ? '' : undefined}
      actions={
        <>
          <span className="card-state" title={i.open ? 'Needs you' : 'Answered'}>
            <Icon name={i.open ? 'waiting' : 'answered'} size={14} label={i.open ? 'Needs you' : 'Answered'} />
          </span>
          <span className="muted card-time hidden group-hover:inline group-focus-within:inline">{clockTime(i.at)}</span>
        </>
      }
    >
      <span className="flex items-center gap-1.5 overflow-hidden whitespace-nowrap">
        {ans && p?.kind === 'permission' ? <span className="flex-none text-ink">{ans.label}</span> : null}
        {p?.kind === 'permission' ? <span className="mono">{p.tool}</span> : <span className="muted">{p?.kind}</span>}
        {p?.command ? <CardCode>{p.command}</CardCode> : null}
        {ans && p?.kind !== 'permission' ? (
          <span className="min-w-0 truncate text-ink">
            → {ans.label}
            {ans.note ? `: ${ans.note}` : ''}
          </span>
        ) : null}
        {i.answer ? (
          <span className="flex-none text-ink-3">
            {ans ? '' : 'answered elsewhere '}· {ago(i.answer.at)}
          </span>
        ) : null}
      </span>
    </Notice>
  );
}
