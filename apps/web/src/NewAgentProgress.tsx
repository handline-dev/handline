// The progress of one agent.create, from the operations topic: the step the daemon sent (none invented), then done or failed. A failed
// creation offers the form back with its fields; a done one sends the first task, if there was one, as a task.new.
import { useState } from 'react';
import { classify, sentence, Skeleton, useCreateProgress, type NewAgentFields } from '@handline/app-data';
import { Button, Dialog, Icon, Textarea, NOTE } from '#src/ui/index.ts';
import { goAgent, goNew } from '#src/route.ts';
import { browserStorage } from '#src/daemon.ts';

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
  fields: NewAgentFields;
  onClose: () => void;
  onAnother: () => void;
}) {
  const [first] = useState(() => (fields.task.trim() ? fields : { ...fields, ...kept(operation) }));
  const { q, op, agent, text, setText, ask, send } = useCreateProgress(operation, first, forgetFirstTask);
  if (!op)
    return (
      <Dialog title="New agent" onClose={onClose}>
        {q.isError ? (
          <p className={`${NOTE} text-bad`} role="alert">
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
        <div className="na-progress grid gap-2" role="status">
          {op.step ? <p>{op.step}</p> : null}
          {op.steps_total ? (
            <>
              <progress className="w-full" max={op.steps_total} value={op.steps_done} aria-label="Steps done" />
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
        <p className={`${NOTE} text-bad`} role="alert">
          <Icon name="failed" size={14} label="Failed" />
          {op.message ?? 'Creating the agent failed.'}
        </p>
        {registered && agent ? <p>{agent} is registered, not started.</p> : null}
        <div className="na-actions mt-3 flex justify-end gap-2">
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
          <p className={`${NOTE} text-bad`} role="alert">
            <Icon name="failed" size={14} label="Error" />
            {sentence(classify(ask.error))}
          </p>
          <Textarea className="bg-panel border-line-strong px-3 py-2 resize-y" aria-label="First task" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          <Button variant="outline" label="Retry" disabled={ask.isPending} onClick={send} />
        </>
      ) : null}
      <div className="na-actions mt-3 flex justify-end gap-2">
        {agent ? <Button variant="solid" label="Open agent" onClick={() => (onClose(), goAgent(agent))} /> : null}
        <Button label="Create another" onClick={onAnother} />
      </div>
    </Dialog>
  );
}
