// A decided HR proposal as one line, opening to its card.
import { useState, type ReactNode } from 'react';
import { dateTime, kindOf, stateWord, textSafe } from '@handline/app-core';
import type { Proposal } from '@handline/client';
import { Button, Icon, SectionHead, type IconName } from '#src/ui/index.ts';
import { Reveal } from '#src/ui/Reveal.tsx';

export const DONE_SHOWN = 10;
const DONE_ICON: Record<string, IconName> = { approved: 'done', rejected: 'failed' };

/** A decided proposal in one line: the result (an icon, its word in the title), the kind, what it was, the agent and the day; a click opens its card. */
export function DoneRow({ p, open, onToggle, children }: { p: Proposal; open: boolean; onToggle: () => void; children: ReactNode }) {
  const { word } = stateWord(p);
  return (
    <div className="hr-done" data-done={p.id}>
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full cursor-pointer items-center gap-2 rounded-md border border-line bg-surface px-3 py-1.5 text-left text-md"
        onClick={onToggle}
      >
        <Icon name={DONE_ICON[p.state] ?? 'idle'} size={14} label={word} />
        {kindOf(p) ? <span className="hr-kind rounded-sm border border-line px-1.5 text-sm text-ink-2">{kindOf(p)}</span> : null}
        <span className="min-w-0 flex-1 truncate">{textSafe(p.title)}</span>
        <span className="muted truncate">{textSafe(p.agent)}</span>
        <span className="muted tabular-nums">{dateTime(p.settled ?? p.created)}</span>
      </button>
      <Reveal show={open}>
        <div className="pt-2">{children}</div>
      </Reveal>
    </div>
  );
}

/** The decided proposals: the first ten as rows, the rest behind a button (a link to one of them shows all), each opening to `children(p)`. */
export function DoneList({ recent, focus, children }: { recent: Proposal[]; focus: number | null; children: (p: Proposal) => ReactNode }) {
  const [older, setOlder] = useState(false);
  const [opened, setOpened] = useState<ReadonlySet<number>>(new Set());
  const flip = (id: number) =>
    setOpened((s) => {
      const n = new Set(s);
      if (!n.delete(id)) n.add(id);
      return n;
    });
  const everything = older || recent.findIndex((p) => p.id === focus) >= DONE_SHOWN;
  return (
    <>
      {recent.length ? <SectionHead variant="small">Done</SectionHead> : null}
      {(everything ? recent : recent.slice(0, DONE_SHOWN)).map((p) => (
        <DoneRow key={p.id} p={p} open={opened.has(p.id) || focus === p.id} onToggle={() => flip(p.id)}>
          {children(p)}
        </DoneRow>
      ))}
      {!everything && recent.length > DONE_SHOWN ? (
        <Button variant="ghost" size="sm" label={`Show ${recent.length - DONE_SHOWN} older`} onClick={() => setOlder(true)} />
      ) : null}
    </>
  );
}
