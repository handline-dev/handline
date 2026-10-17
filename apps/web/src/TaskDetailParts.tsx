// The task detail's presentational parts: the meta grid, one verb button, the link and timeline sections, and the two small sheets.
import { useState } from 'react';
import { dateTime, taskRef, timeBoxText } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { Icon, type IconName } from './ui/Icon.tsx';
import { Sheet } from './taskui.tsx';
import { AgentLink, Inline, PriorityInput, TextInput, useEditTask } from './TaskInline.tsx';
import { Button, Input, MetaField, MetaGrid } from './ui/index.ts';

export const TIMELINE = 'ttimeline m-0 list-none p-0 [&_svg]:shrink-0 [&_svg]:self-center [&_svg]:text-ink-3';

const H3 = 'mt-4 mb-1 flex items-center text-ink-3';
const LI = 'flex items-baseline gap-1.5 py-0.5';

/** The fields; the owner, priority and time box change where they are (the owner through the assign sheet). */
export function TaskMeta({ t, now, onAssign }: { t: Detail; now: number; onAssign: () => void }) {
  const edit = useEditTask(t);
  const open = t.state !== 'done' && t.state !== 'discarded';
  const can = edit.can.ok && open;
  const box = timeBoxText(t, now);
  return (
    <>
      <MetaGrid>
        <MetaField
          icon="owner"
          label="Owner"
          value={
            <>
              <strong>
                <AgentLink name={t.to} />
              </strong>
              {can ? <Button icon="edit" size="sm" label="Change the owner" onClick={onAssign} /> : null}
            </>
          }
        />
        <MetaField icon="agent" label="Asked by" value={<strong><AgentLink name={t.from} /></strong>} />
        <MetaField
          icon="priority"
          label="Priority"
          value={
            <Inline can={can} label="Priority" input={(close) => <PriorityInput value={t.priority ?? 2} onSave={(priority) => edit.save({ priority })} onClose={close} />}>
              {`P${t.priority ?? 2}`}
            </Inline>
          }
        />
        <MetaField
          icon="waiting"
          label="Time box"
          value={
            <Inline
              can={can}
              label="Time box"
              input={(close) => <TextInput value={t.time_box ?? ''} label="Time box" onSave={(time_box) => edit.save({ time_box })} onClose={close} />}
            >
              {box || 'none'}
            </Inline>
          }
        />
        <MetaField icon="schedules" label="Created" value={dateTime(t.created)} />
        {t.delivered ? <MetaField icon="delivered" label="Delivered" value={dateTime(t.delivered)} /> : null}
        {t.done ? <MetaField icon="taskDone" label="Closed" value={dateTime(t.done)} /> : null}
        {t.parent ? (
          <MetaField
            icon="viewTree"
            label="Under"
            value={
              <a href={`#/tasks/${t.parent}`} className="mono">
                {taskRef(t.parent)}
              </a>
            }
          />
        ) : null}
      </MetaGrid>
      {edit.error ? (
        <p className="m-0 text-bad" role="alert">
          {edit.error}
        </p>
      ) : null}
    </>
  );
}

export function Verb(props: {
  k: string;
  icon: IconName;
  label: string;
  can: { ok: boolean; why?: string };
  onClick: () => void;
  busy?: boolean;
}) {
  return (
    <Button
      icon={props.busy ? undefined : props.icon}
      label={props.label}
      aria-label={props.label}
      title={props.can.why ?? `${props.label} (${props.k})`}
      disabled={!props.can.ok || props.busy}
      className="max-sm:min-h-[44px]"
      onClick={props.onClick}
    >
      {props.busy ? '…' : props.label}
    </Button>
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
            <a href={`#/tasks/${b.task}`} className="mono text-inherit no-underline hover:underline">
              {taskRef(b.task)}
            </a>{' '}
            {b.reason !== undefined ? <span className="muted">{b.reason}</span> : null}
          </li>
        ))}
      </ul>
    </>
  );
}

export function TaskTimeline({ deliveries, events }: { deliveries: Detail['deliveries']; events: Detail['events'] }) {
  return (
    <ol className={TIMELINE}>
      {deliveries.map((d) => (
        <li key={d.at} title={`delivered by ${d.how}`} className={LI}>
          <Icon name="delivered" label="Delivered" aria-hidden="true" size={14} />
          <span className="mono">{dateTime(d.at)}</span> Delivered ({d.how}): {d.outcome}
        </li>
      ))}
      {events.map((e, i) => (
        <li key={i} className={LI}>
          <Icon name="changed" label="Event" aria-hidden="true" size={14} />
          <span className="mono">{dateTime(e.at)}</span> {e.type}
          {e.agent ? <> · <AgentLink name={e.agent} /></> : null}
        </li>
      ))}
    </ol>
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
