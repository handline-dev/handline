// The progress of one agent.create, from the operations topic: the step the daemon sent (none invented), then done or failed. A failed
// creation offers the form back with its fields; a done one sends the first task, if there was one, as a task.new.
import { useEffect, useRef, useState } from 'react';
import { classify, sentence, Skeleton, useOperation, useWrite } from '@handline/app-data';
import { Button, Dialog, Icon, Textarea } from './ui/index.ts';
import { goAgent, goNew } from './route.ts';
import { browserStorage } from './daemon.ts';
import type { Fields } from './NewAgent.tsx';

const KEY = 'handline.new-agent.task';
/** The first task typed for one operation, kept so a relaunch of `#/new/:operation` still sends it. */
export function keepFirstTask(operation: string, task: string, box: string): void {
  try {
    if (task.trim()) browserStorage()?.setItem(KEY, JSON.stringify({ operation, task, box }));
  } catch {
    /* blocked: the task is then only in this page */
  }
}
function kept(operation: string): { task: string; box: string } | null {
  try {
    const s = JSON.parse(browserStorage()?.getItem(KEY) ?? 'null') as { operation?: string; task?: string; box?: string } | null;
    return s?.operation === operation && s.task ? { task: s.task, box: s.box ?? '' } : null;
  } catch {
    return null;
  }
}
export function forgetFirstTask(): void {
  try {
    browserStorage()?.setItem(KEY, '');
  } catch {
    /* blocked */
  }
}

export function NewAgentProgress({
  operation,
  fields,
  onClose,
  onAnother,
}: {
  operation: string;
  fields: Fields;
  onClose: () => void;
  onAnother: () => void;
}) {
  const q = useOperation(operation);
  const op = q.data;
  const ask = useWrite('task.new');
  const sent = useRef(false);
  const [first] = useState(() => (fields.task.trim() ? fields : { ...fields, ...kept(operation) }));
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
      { onSuccess: forgetFirstTask },
    );
  };
  useEffect(() => {
    if (done && !sent.current && text.trim()) {
      sent.current = true;
      send();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the operation turns done
  }, [done]);
  if (!op)
    return (
      <Dialog title="New agent" onClose={onClose}>
        {q.isError ? (
          <p className="ui-note bad" role="alert">
            <Icon name="failed" size={14} label="Error" />
            {sentence(classify(q.error))} <Button variant="outline" label="Retry" onClick={() => void q.refetch()} />
          </p>
        ) : (
          <Skeleton shape="agentDetail" loading label="the new agent" />
        )}
      </Dialog>
    );
  if (op.state === 'running')
    return (
      <Dialog title={`Creating ${agent ?? 'the agent'}`} onClose={onClose}>
        <div className="na-progress" role="status">
          {op.step ? <p>{op.step}</p> : null}
          {op.steps_total ? (
            <>
              <progress max={op.steps_total} value={op.steps_done} aria-label="Steps done" />
              <p className="muted">
                step {Math.min(op.steps_done + 1, op.steps_total)} of {op.steps_total}
              </p>
            </>
          ) : null}
          {op.message ? <p className="muted">{op.message}</p> : null}
        </div>
      </Dialog>
    );
  if (op.state === 'failed') {
    const registered = (op.result as { registered?: boolean } | null)?.registered === true;
    return (
      <Dialog title="New agent" onClose={onClose}>
        <p className="ui-note bad" role="alert">
          <Icon name="failed" size={14} label="Failed" />
          {op.message ?? 'Creating the agent failed.'}
        </p>
        {registered && agent ? <p>{agent} is registered, not started.</p> : null}
        <div className="na-actions">
          {registered && agent ? <Button variant="outline" label="Open agent" onClick={() => (onClose(), goAgent(agent))} /> : null}
          <Button variant="solid" label="Edit and try again" onClick={() => goNew()} />
        </div>
      </Dialog>
    );
  }
  const taskRefused = ask.isError;
  return (
    <Dialog title="New agent" onClose={onClose}>
      <p role="status">
        {agent} is running{taskRefused ? ', but the first task was not sent' : ''}.
      </p>
      {taskRefused ? (
        <>
          <p className="ui-note bad" role="alert">
            <Icon name="failed" size={14} label="Error" />
            {sentence(classify(ask.error))}
          </p>
          <Textarea className="bg-panel border-line-strong px-5 py-4 resize-y" aria-label="First task" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          <Button variant="outline" label="Retry" disabled={ask.isPending} onClick={send} />
        </>
      ) : null}
      <div className="na-actions">
        {agent ? <Button variant="solid" label="Open agent" onClick={() => (onClose(), goAgent(agent))} /> : null}
        <Button label="Create another" onClick={onAnother} />
      </div>
    </Dialog>
  );
}
