// The small button under a founder's comment that told agents: how many have read it, a clock while one is held, and a popover with each reader.
import { useState } from 'react';
import type { MemoReader } from '@handline/client';
import { ago } from '@handline/app-core';
import { classify, sentence, useOff, useWrite } from '@handline/app-data';
import { Button, Icon, Popup } from '#src/ui/index.ts';

const stateText = (r: MemoReader): string =>
  r.state === 'read' ? `read${r.at ? ` ${ago(r.at)}` : ''}` : r.state === 'sent' ? 'sent, not read' : `held, about ${r.minutes ?? 1} min`;

/** Nothing for a comment every reader has read: the button goes when there is nothing left to learn. */
export function CommentReaders({ memo, readers }: { memo: number; readers: MemoReader[] }) {
  const [open, setOpen] = useState(false);
  const send = useWrite('task.comment.send_now');
  const off = useOff('task.comment.send_now');
  const read = readers.filter((r) => r.state === 'read').length;
  if (!readers.length || read === readers.length) return null;
  const held = readers.some((r) => r.state === 'held');
  return (
    <span className="relative self-start text-hint text-ink-3">
      <button
        type="button"
        className="comment-readers flex items-center gap-1 rounded-card border-0 bg-transparent px-1 text-hint text-ink-3 tint"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {held ? <Icon name="waiting" size={12} aria-hidden /> : null}
        {read}/{readers.length} read
      </button>
      <Popup open={open} onClose={() => setOpen(false)} title="Who has read this" up>
        <ul className="m-0 grid list-none gap-0.5 p-1">
          {readers.map((r) => (
            <li key={r.agent} className="flex h-7 items-center gap-2 px-1" data-reader={r.agent}>
              <span className="mono flex-1 truncate">{r.agent}</span>
              <span className="text-ink-3">{stateText(r)}</span>
              {r.state === 'held' ? (
                <Button
                  size="sm"
                  label="Send now"
                  title={off}
                  disabled={!!off || send.isPending}
                  onClick={() => send.mutate({ memo, agent: r.agent })}
                >
                  Send now
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
        {send.isError ? (
          <p className="m-0 px-2 pb-1 text-hint text-bad" role="alert">
            {sentence(classify(send.error))}
          </p>
        ) : null}
      </Popup>
    </span>
  );
}
