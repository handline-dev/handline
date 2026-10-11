// The task detail's presentational parts: the meta grid, one verb button, the link and timeline sections, and the two small sheets.
import { useState, type ReactNode } from 'react';
import { timeoutText } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { useWrite } from '@handline/app-data';
import { Icon, type IconName } from './ui/Icon.tsx';
import { Sheet, VH } from './taskui.tsx';
import { when } from './TaskMemos.tsx';

const H3 = 'mt-4 mb-1 flex items-center text-ink-3';
const LI = 'flex items-baseline gap-1.5 py-0.5';
const INPUT = 'tinput min-w-40 flex-1 rounded-ctl border border-line px-2 py-1.5 bg-canvas text-ink';
const SELECT = 'h-[28px] rounded-ctl border border-line bg-panel px-1.5 text-body text-ink';

export function TaskMeta({ t, now }: { t: Detail; now: number }) {
  return (
    <dl className="tmeta grid grid-cols-2 gap-x-4 gap-y-1.5 my-2">
      <Field icon="owner" label="Owner" value={<strong>{t.to}</strong>} />
      <Field icon="agent" label="Asked by" value={<strong>{t.from}</strong>} />
      <Field icon="priority" label="Priority" value={`P${t.priority ?? 2}`} />
      <Field icon="waiting" label="Time box" value={[t.time_box ?? 'none', timeoutText(t, now)].filter(Boolean).join(' · ')} />
      <Field icon="schedules" label="Created" value={when(t.created)} />
      {t.delivered ? <Field icon="delivered" label="Delivered" value={when(t.delivered)} /> : null}
      {t.done ? <Field icon="taskDone" label="Closed" value={when(t.done)} /> : null}
      {t.parent ? (
        <Field
          icon="viewTree"
          label="Under"
          value={
            <a href={`#/tasks/${t.parent}`} className="mono">
              #{t.parent}
            </a>
          }
        />
      ) : null}
    </dl>
  );
}

function Field({ icon, label, value }: { icon: IconName; label: string; value: ReactNode }) {
  return (
    <div className="tfield2 flex items-center gap-1.5 min-w-0 text-ink-2" title={label}>
      <dt className="m-0 flex min-w-0">
        <Icon name={icon} label={label} aria-hidden="true" />
        <span className={`vh ${VH}`}>{label}</span>
      </dt>
      <dd className="m-0 flex min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-ink">{value}</dd>
    </div>
  );
}

export function Verb(props: { k: string; icon: IconName; label: string; can: { ok: boolean; why?: string }; onClick: () => void; busy?: boolean }) {
  return (
    <button
      className="btn icon"
      disabled={!props.can.ok || props.busy}
      title={props.can.why ?? `${props.label} (${props.k})`}
      aria-label={props.label}
      onClick={props.onClick}
    >
      {props.busy ? '…' : <Icon name={props.icon} label={props.label} aria-hidden="true" />}
    </button>
  );
}

/** The waits-on and sub-task lists: one heading, one link per row. */
export function TaskLinks({ icon, label, items }: { icon: IconName; label: string; items: { task: number; reason?: string }[] }) {
  return (
    <>
      <h3 className={H3}>
        <Icon name={icon} label={label} aria-hidden="true" />
        <span className={`vh ${VH}`}>{label}</span>
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
    <ol className="ttimeline m-0 list-none p-0 [&_svg]:shrink-0 [&_svg]:self-center [&_svg]:text-ink-3">
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
      <input className={INPUT} value={reason} aria-label="Reason" onChange={(e) => setReason(e.target.value)} />
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
          <input className={INPUT} value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Owner
          <input className={INPUT} value={owner} onChange={(e) => setOwner(e.target.value)} />
        </label>
        <label className="wide">
          Body
          <textarea className={INPUT} rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <label>
          Time box
          <input className={INPUT} value={box} onChange={(e) => setBox(e.target.value)} />
        </label>
        <label>
          Priority
          <select className={SELECT} value={priority} onChange={(e) => setPriority(Number(e.target.value) as 1 | 2 | 3)}>
            <option value={1}>1 high</option>
            <option value={2}>2</option>
            <option value={3}>3 low</option>
          </select>
        </label>
      </div>
    </Sheet>
  );
}
