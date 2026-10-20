// The progress of one agent.create and its first task, for the PC and the phone: the operation, and once it turns done the first task
// the person typed, sent as a task.new to the new agent (once).
import { useEffect, useRef, useState } from 'react';
import { useOperation } from '#src/read.ts';
import { useWrite } from '#src/write.ts';

export function useCreateProgress(operation: string, first: { task: string; box: string }, onSent: (() => void) | undefined) {
  const q = useOperation(operation);
  const op = q.data;
  const ask = useWrite('task.new');
  const sent = useRef(false);
  const [text, setText] = useState(first.task);
  const agent = op?.agent ?? null;
  const done = op?.state === 'done';
  const send = () => {
    const [title = '', ...rest] = text.trim().split('\n');
    if (!agent || !title) return;
    ask.mutate(
      {
        owner: agent,
        title: title.slice(0, 80),
        body: rest.join('\n').trim(),
        time_box: first.box.trim() || '2h',
        request_id: `first-task:${operation}`.slice(0, 128),
      },
      { onSuccess: () => onSent?.() },
    );
  };
  useEffect(() => {
    if (done && !sent.current && text.trim()) {
      sent.current = true;
      send();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the operation turns done
  }, [done]);
  return { q, op, agent, done, text, setText, ask, send };
}
