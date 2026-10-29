// The line at the bottom of a chat while the agent works (T5611): how long, how many commands and what it runs now. A click opens this turn's
// tool lines; when the turn is over it stays as "Ran N commands" in the same place.
import { motion, useReducedMotion } from 'framer-motion';
import { useEffect, useState } from 'react';
import type { ChatMessage, TranscriptItem, Working } from '@handline/client';
import { turnMinutes, turnSummary } from '@handline/app-core';
import { Item } from '#src/ChatMessages.tsx';
import { Icon } from '#src/ui/index.ts';

/** Three dots that rise in turn. */
function Dots() {
  const reduced = useReducedMotion();
  return (
    <span className="inline-flex items-center gap-[3px]" aria-hidden="true">
      {[0, 1, 2].map((n) => (
        <motion.span
          key={n}
          className="block size-[5px] rounded-full bg-current"
          animate={reduced ? undefined : { y: [0, -3, 0], opacity: [0.4, 1, 0.4] }}
          transition={{ duration: 0.9, repeat: Infinity, delay: n * 0.15, ease: 'easeInOut' }}
        />
      ))}
    </span>
  );
}

const commands = (n: number) => `${n} command${n === 1 ? '' : 's'}`;

export function LiveLine({ working, messages, items }: { working: Working; messages: ChatMessage[]; items: TranscriptItem[] }) {
  const [open, setOpen] = useState(false);
  const [, tick] = useState(0);
  const running = working.turn_running;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, [running]);
  const turn = turnSummary(messages, items, running);
  if (!running && !turn.count) return <div className="working-slot h-5 flex-none" />; // the row stays so the messages above never move
  const now = turn.last ?? working.line;
  const text = running
    ? ['Working', turnMinutes(working.since), turn.count ? commands(turn.count) : null, now ? `now: ${now}` : null]
        .filter(Boolean)
        .join(' · ')
    : `Ran ${commands(turn.count)}`;
  return (
    <div className="working-slot flex min-h-5 flex-none flex-col items-start self-start">
      <button
        type="button"
        className={`line ${running ? 'working' : 'ran'} flex h-5 max-w-full cursor-pointer items-center gap-1.5 border-0 bg-transparent p-0 text-xs ${running ? 'text-working' : 'text-ink-2'}`}
        role="status"
        aria-expanded={open}
        title={text}
        onClick={() => setOpen(!open)}
      >
        {running ? <Dots /> : null}
        <span className="truncate">{text}</span>
        {turn.count ? <Icon name={open ? 'less' : 'more'} size={14} aria-hidden /> : null}
      </button>
      {open ? (
        <div className="turn-steps flex w-full flex-col gap-1 py-1" data-testid="turn-steps">
          {turn.steps.map((i) => (
            <Item key={i.id} i={i} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
