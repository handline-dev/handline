// Later on Requests: the button beside an item's answers (a few quick times, a time of the person's own, or when a task is done), and the fold at the
// foot of the list that holds what is put off, each row ending with when it returns. A prompt or an HR proposal has no Later: an agent is stopped on it.
import { useEffect, useState } from 'react';
import { laterChoices, laterKeyOf, laterWords, type Card, type LaterAt } from '@handline/app-core';
import { useLater, useTasks } from '@handline/app-data';
import { sourceWord } from '#src/requestsText.ts';
import { Chip } from '#src/ds/Chip.tsx';
import { ListRow } from '#src/ds/ListRow.tsx';
import { Button, Dialog, FIELD, FIELD_LABEL, Icon, Input, MenuButton, MenuItem, Select } from '#src/ui/index.ts';

const inputTime = (ms: number) => {
  const d = new Date(ms);
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};

/** A time of the person's own, in a form: a date and a time in place. */
function PickTime({ now, onPut, onClose }: { now: number; onPut: (at: LaterAt) => void; onClose: () => void }) {
  const [when, setWhen] = useState(() => inputTime(now + 3_600_000));
  const ok = Number.isFinite(Date.parse(when)) && Date.parse(when) > now;
  return (
    <Dialog title="Later until" onClose={onClose} action={{ label: 'Later', form: 'later-time', disabled: !ok }}>
      <form
        id="later-time"
        name="later-time"
        onSubmit={(e) => {
          e.preventDefault();
          if (!ok) return;
          onPut({ until: new Date(when).toISOString() });
          onClose();
        }}
      >
        <div className={FIELD}>
          <span className={FIELD_LABEL}>Date and time</span>
          <Input type="datetime-local" aria-label="Date and time" value={when} onChange={(e) => setWhen(e.target.value)} />
        </div>
      </form>
    </Dialog>
  );
}

/** The open task whose end brings the item back. */
function AfterTask({ own, onPut, onClose }: { own: number | null; onPut: (at: LaterAt) => void; onClose: () => void }) {
  const [task, setTask] = useState('');
  const open = (useTasks({}).data?.items ?? []).filter((t) => t.id !== own && !['done', 'discarded'].includes(t.state));
  return (
    <Dialog title="Later, when a task is done" onClose={onClose} action={{ label: 'Later', form: 'later-task', disabled: !task }}>
      <form
        id="later-task"
        name="later-task"
        onSubmit={(e) => {
          e.preventDefault();
          if (!task) return;
          onPut({ after_task: Number(task) });
          onClose();
        }}
      >
        <div className={FIELD}>
          <span className={FIELD_LABEL}>Task</span>
          <Select
            label="Task to wait for"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            options={[{ value: '', label: 'pick a task' }, ...open.map((t) => ({ value: String(t.id), label: `T${t.id} ${t.title}` }))]}
          />
        </div>
      </form>
    </Dialog>
  );
}

/** "Later" beside the answers; once the item is put off it reads "Later: Fri 09:00" with a × that returns it at once. */
export function LaterButton(props: { card: Card; at: LaterAt | null; now: number; onPut: (at: LaterAt | null) => void }) {
  const { card, at, now, onPut } = props;
  const [form, setForm] = useState<'time' | 'task' | null>(null);
  return (
    <>
      <MenuButton
        label={at ? `Later: ${laterWords(at)}` : 'Later'}
        align="start"
        trigger={
          <span className="inline-flex items-center gap-1.5">
            <Icon name="waiting" size={14} aria-hidden />
            {at ? `Later: ${laterWords(at)}` : 'Later'}
          </span>
        }
      >
        {laterChoices(now).map((c) => (
          <MenuItem key={c.label} onSelect={() => onPut({ until: c.until })}>
            {c.label}
          </MenuItem>
        ))}
        <MenuItem onSelect={() => setForm('time')}>Pick a time…</MenuItem>
        <MenuItem onSelect={() => setForm('task')}>When a task is done…</MenuItem>
      </MenuButton>
      {at ? <Button icon="close" size="sm" label="Back to Requests now" title="Back to Requests now" onClick={() => onPut(null)} /> : null}
      {form === 'time' ? <PickTime now={now} onPut={onPut} onClose={() => setForm(null)} /> : null}
      {form === 'task' ? <AfterTask own={card.task} onPut={onPut} onClose={() => setForm(null)} /> : null}
    </>
  );
}

/** "Later N" at the foot of the list: opens in place, each row ending with when it returns. */
export function LaterFold({
  items,
  selected,
  onOpen,
}: {
  items: { card: Card; at: LaterAt }[];
  selected: string | undefined;
  onOpen: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  return (
    <section aria-label="Later" className="later-fold mt-2">
      <button
        type="button"
        className="flex items-center gap-1.5 border-0 bg-transparent px-2 py-1.5 text-sm font-semibold text-ink"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="chevronRight" size={12} aria-hidden className={open ? 'rotate-90' : undefined} /> Later{' '}
        <span className="font-normal text-ink-3">{items.length}</span>
      </button>
      {open
        ? items.map(({ card, at }) => (
            <div key={card.key} data-card-row={card.key} data-later>
              <ListRow
                title={card.title}
                chip={<Chip>{sourceWord(card)}</Chip>}
                time={laterWords(at)}
                selected={selected === card.key}
                label={card.title}
                onOpen={() => onOpen(card.key)}
              />
            </div>
          ))
        : null}
    </section>
  );
}

/** Puts an item off, or returns it, at once; the toast it leaves says when, and its Undo puts the item back as it was. */
export function useLaterToast(deferred: { card: Card; at: LaterAt }[]) {
  const setLater = useLater();
  const [undo, setUndo] = useState<{ line: string; run: () => void } | null>(null);
  useEffect(() => {
    if (!undo) return;
    const t = setTimeout(() => setUndo(null), 6000);
    return () => clearTimeout(t);
  }, [undo]);
  return {
    undo,
    runUndo: () => (undo?.run(), setUndo(null)),
    put: (card: Card, at: LaterAt | null) => {
      const key = laterKeyOf(card);
      if (!key) return;
      const before = deferred.find((l) => l.card.key === card.key)?.at ?? null;
      setLater(key, at);
      setUndo({ line: at ? `Later: ${laterWords(at)}` : 'Back in Requests', run: () => setLater(key, before) });
    },
  };
}
