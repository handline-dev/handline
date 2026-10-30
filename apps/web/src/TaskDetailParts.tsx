// The task detail's presentational parts: the assignee, priority and time box chips, the link sections and the cancel sheet.
import { useState, type ReactNode } from 'react';
import { isGate, taskRef, timeBoxText, watchLine } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { Icon, type IconName } from '#src/ui/Icon.tsx';
import { Sheet } from '#src/taskui.tsx';
import { AssignPopover } from '#src/TaskPeople.tsx';
import { Inline, TextInput, useEditTask } from '#src/TaskInline.tsx';
import { Input, MenuButton, MenuItem } from '#src/ui/index.ts';
import { Button as DsButton } from '#src/ds/Button.tsx';
import { Chip } from '#src/ds/Chip.tsx';
import { Id } from '#src/ds/Link.tsx';

const H3 = 'm-0 mt-4 mb-1 flex items-center text-sm font-semibold';
const LI = 'flex items-baseline gap-1.5 py-0.5';

/** The assignee, priority and time box as chips: the first two open a choice, the time box edits where it is. A closed task shows them as plain words. */
export function TaskChips({ t, now, me, lead }: { t: Detail; now: number; me: string; lead?: ReactNode }) {
  const [picking, setPicking] = useState(false);
  const edit = useEditTask(t);
  const can = edit.can.ok && t.state !== 'done' && t.state !== 'discarded' && !isGate(t);
  const box = timeBoxText(t, now);
  const owner = `Assigned to · ${t.to}`;
  const priority = `Priority · P${t.priority ?? 2}`;
  return (
    <>
      <div className="tchips flex flex-wrap items-center gap-2">
        {lead}
        {can ? (
          <>
            <span className="relative inline-flex">
              <DsButton size="s" label="Change who it is assigned to" title={edit.can.why} onClick={() => setPicking(!picking)}>
                {owner} ▾
              </DsButton>
              <AssignPopover t={t} me={me} open={picking} onClose={() => setPicking(false)} />
            </span>
            <MenuButton label="Change the priority" variant="outline" size="sm" align="start" trigger={`${priority} ▾`}>
              {([1, 2, 3] as const).map((p) => (
                <MenuItem key={p} onSelect={() => edit.save({ priority: p })}>
                  {`P${p}${p === 1 ? ' high' : p === 3 ? ' low' : ''}`}
                </MenuItem>
              ))}
            </MenuButton>
          </>
        ) : (
          <>
            <Chip>{owner}</Chip>
            <Chip>{priority}</Chip>
          </>
        )}
        <Chip>
          Time box ·{' '}
          <Inline
            can={can}
            label="Time box"
            input={(close) => (
              <TextInput value={t.time_box ?? ''} label="Time box" onSave={(time_box) => edit.save({ time_box })} onClose={close} />
            )}
          >
            {box || 'none'}
          </Inline>
        </Chip>
      </div>
      {edit.error ? (
        <p className="m-0 text-bad" role="alert">
          {edit.error}
        </p>
      ) : null}
    </>
  );
}

/** The waits-on and sub-task lists: one heading, one link per row. */
export function TaskLinks({ icon, label, items }: { icon: IconName; label: string; items: { task: number; reason?: string }[] }) {
  return (
    <>
      <h3 className={H3}>
        <Icon name={icon} label={label} aria-hidden="true" /> <span className="ml-1">{label}</span>
      </h3>
      <ul className="tlinks m-0 list-none p-0">
        {items.map((b) => (
          <li key={b.task} className={LI}>
            <Id href={`#/tasks/${b.task}`}>{taskRef(b.task)}</Id>{' '}
            {b.reason !== undefined ? <span className="muted">{b.reason}</span> : null}
          </li>
        ))}
      </ul>
    </>
  );
}

export function CancelSheet(props: { running: boolean; id: number; onCancel: () => void; onSubmit: (reason?: string) => void }) {
  const [reason, setReason] = useState('');
  return (
    <Sheet
      label={props.running ? `stop ${taskRef(props.id)}? why (optional):` : `cancel ${taskRef(props.id)}? why (optional):`}
      submit={props.running ? 'Stop it' : 'Cancel task'}
      onCancel={props.onCancel}
      onSubmit={() => props.onSubmit(reason.trim() || undefined)}
    >
      <Input grow className="tinput" value={reason} aria-label="Reason" onChange={(e) => setReason(e.target.value)} />
    </Sheet>
  );
}

/** What a gate task watches, as a link where the vendor has a page for it, with the result once it has one. */
export function TaskWatch({ t }: { t: Detail }) {
  const watch = watchLine(t);
  if (!watch) return null;
  return (
    <p className="twatch m-0 mt-1 text-xs text-ink-3">
      Watches{' '}
      {watch.url ? (
        <a href={watch.url} target="_blank" rel="noreferrer">
          {watch.text}
        </a>
      ) : (
        watch.text
      )}
      {watch.result ? ` · ${watch.result}` : ''}
    </p>
  );
}
