// A prompt the agent asked, as one quiet line: what it asked, and once answered what it got and when.
import { useState } from 'react';
import type { TranscriptItem } from '@handline/client';
import { ago, clockTime } from '@handline/app-core';
import { CardCode, Icon } from '#src/ui/index.ts';
import { Notice } from '#src/ds/Chat.tsx';

export function PromptLine({ i }: { i: TranscriptItem }) {
  const p = i.prompt;
  const [shown, setShown] = useState(false);
  const asked = p?.lines.find((l) => l.trim())?.trim();
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
          {i.open ? (
            <span className="card-state" title="Needs you">
              <Icon name="waiting" size={14} label="Needs you" />
            </span>
          ) : null}
          <span className="muted card-time hidden group-hover:inline group-focus-within:inline">{clockTime(i.at)}</span>
        </>
      }
    >
      <span
        className="flex cursor-pointer items-center gap-1.5 overflow-hidden whitespace-nowrap"
        role="button"
        tabIndex={0}
        aria-expanded={shown}
        onClick={() => setShown(!shown)}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setShown(!shown)}
      >
        {ans && p?.kind === 'permission' ? <span className="flex-none text-ink">{ans.label}</span> : null}
        {p?.kind === 'permission' ? (
          <span className="mono">{p.tool}</span>
        ) : (
          <span className="muted min-w-0 truncate">{asked ?? p?.kind}</span>
        )}
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
      {shown && p?.lines.length ? <pre className="mono m-0 mt-1 whitespace-pre-wrap text-xs text-ink-2">{p.lines.join('\n')}</pre> : null}
    </Notice>
  );
}
