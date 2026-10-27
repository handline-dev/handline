// The sheet a task's verb asks with, under the toolbar: why it is done, reopened, discarded, withdrawn, cancelled or released, or who it goes to.
import type { TaskDetail as Detail } from '@handline/client';
import { taskRef } from '@handline/app-core';
import { useWrite } from '@handline/app-data';
import { AnimatedList } from '#src/AnimatedList.tsx';
import { ReasonSheet, Sheet } from '#src/taskui.tsx';
import { CancelSheet } from '#src/TaskDetailParts.tsx';

export type Ask = null | 'done' | 'reopen' | 'discard' | 'cancel' | 'withdraw' | 'release' | 'assign';

/** For the founder every state verb acts at once, with no box; the daemon writes its own word (Done, Discarded). Anyone else is asked first. */
export function useActNow(t: Detail, cancel: ReturnType<typeof useWrite<'task.cancel'>>) {
  const done = useWrite('task.done');
  const reopen = useWrite('task.reopen');
  const discard = useWrite('task.discard');
  const withdraw = useWrite('task.withdraw');
  const release = useWrite('task.release');
  const task = t.id;
  const now = { done, reopen, discard, withdraw, cancel, release } as const;
  return (ask: Exclude<Ask, null | 'assign'>) => now[ask].mutate({ task });
}

export function AskSheets({
  t,
  ask,
  running,
  cancel,
  onClose,
}: {
  t: Detail;
  ask: Ask;
  running: boolean;
  cancel: ReturnType<typeof useWrite<'task.cancel'>>;
  onClose: () => void;
}) {
  const done = useWrite('task.done');
  const reopen = useWrite('task.reopen');
  const discard = useWrite('task.discard');
  const withdraw = useWrite('task.withdraw');
  const release = useWrite('task.release');
  const reasonLabel = (verb: string) => (running ? `stop it and discard - why:` : `${verb} - why:`);
  const then = <A,>(m: { mutate: (a: A) => void }, a: A) => (onClose(), m.mutate(a));
  return (
    <AnimatedList>
      {ask === 'done' ? (
        <ReasonSheet
          key="done"
          label="done: what came of it?"
          submit="Done"
          onCancel={onClose}
          onSubmit={(evidence) => then(done, { task: t.id, evidence, reason: true })}
        />
      ) : null}
      {ask === 'reopen' ? (
        <ReasonSheet
          key="reopen"
          label="reopen: why?"
          submit="Reopen"
          onCancel={onClose}
          onSubmit={(note) => then(reopen, { task: t.id, note })}
        />
      ) : null}
      {ask === 'discard' ? (
        <ReasonSheet
          key="discard"
          label={reasonLabel('discard')}
          submit="Discard"
          onCancel={onClose}
          onSubmit={(reason) => then(discard, { task: t.id, reason })}
        />
      ) : null}
      {ask === 'withdraw' ? (
        <Sheet
          key="withdraw"
          label={`withdraw ${taskRef(t.id)}?`}
          submit="Withdraw"
          onCancel={onClose}
          onSubmit={() => then(withdraw, { task: t.id })}
        >
          <span className="muted">Takes the task back before anyone has started it.</span>
        </Sheet>
      ) : null}
      {ask === 'cancel' ? (
        <CancelSheet
          key="cancel"
          running={running}
          id={t.id}
          onCancel={onClose}
          onSubmit={(reason) => then(cancel, { task: t.id, reason })}
        />
      ) : null}
      {ask === 'release' ? (
        <ReasonSheet
          key="release"
          label={`release ${taskRef(t.id)} - why:`}
          submit="Release"
          onCancel={onClose}
          onSubmit={(reason) => then(release, { task: t.id, reason })}
        />
      ) : null}
    </AnimatedList>
  );
}
