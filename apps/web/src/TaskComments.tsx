// The comments of a task (the daemon's task memos): oldest first, each with who wrote it and when, and a composer under them. `@agent` in a
// comment tells that agent; the assignee is told by default (the daemon's routing). Images go in as in the description.
import { useState, type RefObject } from 'react';
import { ago } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, sentence, useWrite } from '@handline/app-data';
import { BodyEditor } from './BodyEditor.tsx';
import { RichText } from './RichText.tsx';
import { AgentLink } from './TaskInline.tsx';
import { useCan } from './taskui.tsx';
import { Button } from './ui/index.ts';

export function Comments({ t, box }: { t: Detail; box: RefObject<HTMLDivElement | null> }) {
  const can = useCan('task.memo.add');
  const add = useWrite('task.memo.add', String(t.id));
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState(0); // a new editor after a send: its content is the box's own
  const send = () => {
    if (!text.trim() || busy || add.isPending) return;
    add.mutate({ task: t.id, text: text.trim() }, { onSuccess: () => (setText(''), setFresh((n) => n + 1)) });
  };
  return (
    <section className="mt-4 grid gap-2" aria-label="Comments">
      <h3 className="m-0 text-ink-3">Comments {t.memos.length ? t.memos.length : ''}</h3>
      {t.memos.length ? (
        <ul className="tmemos m-0 grid list-none gap-3 p-0">
          {t.memos.map((m) => (
            <li key={m.id} className="comment grid gap-0.5">
              <span className="text-hint text-ink-3">
                <strong className="font-medium text-ink-2">
                  <AgentLink name={m.author} />
                </strong>{' '}
                {ago(m.at)}
                {m.edited_at ? ' · edited' : ''}
              </span>
              <div className="whitespace-pre-wrap">
                <RichText text={m.text} />
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      <div ref={box} className="grid gap-2" title={can.why}>
        {can.ok ? (
          <>
            <BodyEditor key={fresh} value={text} onChange={setText} onBusy={setBusy} label="Comment" />
            <div className="flex items-center gap-2">
              <Button variant="solid" icon="send" label="Comment" disabled={!text.trim() || busy || add.isPending} onClick={send}>
                {add.isPending ? 'Sending…' : 'Comment'}
              </Button>
              <span className="text-hint text-ink-3">@agent asks that agent. The assignee is told either way.</span>
            </div>
          </>
        ) : null}
        {add.isError ? (
          <p className="muted m-0" role="alert">
            {sentence(classify(add.error))}
          </p>
        ) : null}
      </div>
    </section>
  );
}
