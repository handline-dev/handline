// What the task page's ... menu opens: a sub-task, a duplicate, and the pick of a task to wait on.
import { useState } from 'react';
import { taskRef } from '@handline/app-core';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useAgents, useTasks, useWrite } from '@handline/app-data';
import { NewTask } from '#src/NewTask.tsx';
import { Dialog, FIELD, FIELD_LABEL, Icon, NOTE, Select } from '#src/ui/index.ts';

export type TaskDialog = 'sub' | 'dup' | 'wait' | null;

/** A task to wait on, picked from the open ones; the daemon refuses a loop or a closed one and its words show. */
function WaitOn({ t, onClose }: { t: TaskDetail; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const [pick, setPick] = useState('');
  const open = (useTasks({}).data?.items ?? []).filter((x) => x.id !== t.id && !['done', 'discarded'].includes(x.state));
  const failure = edit.isError ? sentence(classify(edit.error)) : '';
  return (
    <Dialog
      title={`${taskRef(t.id)} waits on…`}
      onClose={onClose}
      action={{ label: 'Wait', form: 'waiton-form', disabled: !pick || edit.isPending }}
    >
      <form
        id="waiton-form"
        name="waiton-form"
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (pick) edit.mutate({ task: t.id, blocked_by: Number(pick) }, { onSuccess: onClose });
        }}
      >
        <div className={FIELD}>
          <span className={FIELD_LABEL}>Task</span>
          <Select
            label="Task to wait on"
            value={pick}
            onChange={(e) => setPick(e.target.value)}
            options={[
              { value: '', label: 'pick a task' },
              ...open.map((x) => ({ value: String(x.id), label: `${taskRef(x.id)} ${x.title}` })),
            ]}
          />
        </div>
        {failure ? (
          <p className={`${NOTE} text-bad`} role="alert">
            <Icon name="failed" size={14} label="Error" />
            {failure}
          </p>
        ) : null}
      </form>
    </Dialog>
  );
}

export function TaskDialogs({ t, which, onClose }: { t: TaskDetail; which: TaskDialog; onClose: () => void }) {
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  if (which === 'sub') return <NewTask agents={agents} parent={t.id} start={{ owner: t.to }} onClose={onClose} />;
  if (which === 'dup')
    return <NewTask agents={agents} copy start={{ owner: t.to, title: t.title, body: t.body, priority: t.priority }} onClose={onClose} />;
  if (which === 'wait') return <WaitOn t={t} onClose={onClose} />;
  return null;
}
