// The task detail's presentational parts: the meta grid, one verb button, the link and timeline sections, and the two small sheets.
import { useState } from 'react';
import { timeoutText } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { useWrite } from '@handline/app-data';
import { Icon, type IconName } from './ui/Icon.tsx';
import { Sheet } from './taskui.tsx';
import { Button, Input, MetaField, MetaGrid, NativeSelect, Textarea, VisuallyHidden } from './ui/index.ts';
import { when } from './TaskMemos.tsx';

export const TIMELINE = 'ttimeline m-0 list-none p-0 [&_svg]:shrink-0 [&_svg]:self-center [&_svg]:text-ink-3';

const H3 = 'mt-4 mb-1 flex items-center text-ink-3';
const LI = 'flex items-baseline gap-1.5 py-0.5';

export function TaskMeta({ t, now }: { t: Detail; now: number }) {
  return (
    <MetaGrid>
      <MetaField icon="owner" label="Owner" value={<strong>{t.to}</strong>} />
      <MetaField icon="agent" label="Asked by" value={<strong>{t.from}</strong>} />
      <MetaField icon="priority" label="Priority" value={`P${t.priority ?? 2}`} />
      <MetaField icon="waiting" label="Time box" value={[t.time_box ?? 'none', timeoutText(t, now)].filter(Boolean).join(' · ')} />
      <MetaField icon="schedules" label="Created" value={when(t.created)} />
      {t.delivered ? <MetaField icon="delivered" label="Delivered" value={when(t.delivered)} /> : null}
      {t.done ? <MetaField icon="taskDone" label="Closed" value={when(t.done)} /> : null}
      {t.parent ? (
        <MetaField
          icon="viewTree"
          label="Under"
          value={
            <a href={`#/tasks/${t.parent}`} className="mono">
              #{t.parent}
            </a>
          }
        />
      ) : null}
    </MetaGrid>
  );
}

export function Verb(props: { k: string; icon: IconName; label: string; can: { ok: boolean; why?: string }; onClick: () => void; busy?: boolean }) {
  return (
    <Button
      icon={props.busy ? undefined : props.icon}
      label={props.label}
      aria-label={props.label}
      title={props.can.why ?? `${props.label} (${props.k})`}
      disabled={!props.can.ok || props.busy}
      className={`max-sm:min-h-[44px]${props.busy ? ' w-7 justify-center p-0' : ''}`}
      onClick={props.onClick}
    >
      {props.busy ? '…' : undefined}
    </Button>
  );
}

/** The waits-on and sub-task lists: one heading, one link per row. */
export function TaskLinks({ icon, label, items }: { icon: IconName; label: string; items: { task: number; reason?: string }[] }) {
  return (
    <>
      <h3 className={H3}>
        <Icon name={icon} label={label} aria-hidden="true" />
        <VisuallyHidden>{label}</VisuallyHidden>
      </h3>
      <ul className="tlinks m-0 list-none p-0">
        {items.map((b) => (
          <li key={b.task} className={LI}>
            <a href={`#/tasks/${b.task}`} className="mono text-inherit no-underline hover:underline">
              #{b.task}
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
          <span className="mono">{when(d.at)}</span> {d.how}: {d.outcome}
        </li>
      ))}
      {events.map((e, i) => (
        <li key={i} className={LI}>
          <Icon name="changed" label="Event" aria-hidden="true" size={14} />
          <span className="mono">{when(e.at)}</span> {e.type}
          {e.agent ? ` · ${e.agent}` : ''}
        </li>
      ))}
    </ol>
  );
}

export function CancelSheet(props: { running: boolean; id: number; onCancel: () => void; onSubmit: (reason?: string) => void }) {
  const [reason, setReason] = useState('');
  return (
    <Sheet
      label={props.running ? `stop #${props.id}? why (optional):` : `cancel #${props.id}? why (optional):`}
      submit={props.running ? 'Stop it' : 'Cancel task'}
      onCancel={props.onCancel}
      onSubmit={() => props.onSubmit(reason.trim() || undefined)}
    >
      <Input grow className="tinput" value={reason} aria-label="Reason" onChange={(e) => setReason(e.target.value)} />
    </Sheet>
  );
}

export function EditForm({ t, onClose }: { t: Detail; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const [title, setTitle] = useState(t.title);
  const [body, setBody] = useState(t.body);
  const [owner, setOwner] = useState(t.to);
  const [box, setBox] = useState(t.time_box ?? '');
  const [priority, setPriority] = useState<1 | 2 | 3>(t.priority ?? 2);
  const save = () => {
    const change = {
      ...(title.trim() && title !== t.title ? { title: title.trim() } : {}),
      ...(body !== t.body ? { body } : {}),
      ...(owner.trim() && owner !== t.to ? { owner: owner.trim() } : {}),
      ...(box.trim() && box !== t.time_box ? { time_box: box.trim() } : {}),
      ...(priority !== (t.priority ?? 2) ? { priority } : {}),
    };
    if (!Object.keys(change).length) return onClose();
    edit.mutate({ task: t.id, ...change }, { onSuccess: onClose });
  };
  return (
    <Sheet label={`Edit #${t.id}`} submit="Save" onCancel={onClose} onSubmit={save} busy={edit.isPending}>
      <div className="tform">
        <label>
          Title
          <Input grow className="tinput" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Owner
          <Input grow className="tinput" value={owner} onChange={(e) => setOwner(e.target.value)} />
        </label>
        <label className="wide">
          Body
          <Textarea grow className="tinput" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <label>
          Time box
          <Input grow className="tinput" value={box} onChange={(e) => setBox(e.target.value)} />
        </label>
        <label>
          Priority
          <NativeSelect value={priority} onChange={(e) => setPriority(Number(e.target.value) as 1 | 2 | 3)}>
            <option value={1}>1 high</option>
            <option value={2}>2</option>
            <option value={3}>3 low</option>
          </NativeSelect>
        </label>
      </div>
    </Sheet>
  );
}
