// The agent's schedules: founder-only writes (schedule.add, edit, remove), authored: the line shows at once and reverts on a refusal.
import { useState } from 'react';
import { cronWords } from '@handline/app-core';
import { classify, sentence, useSchedules, useServes, useWrite } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import type { Schedule } from '@handline/client';
import { FNote, SectionHead, Sheet, SheetActions } from './ui/index.ts';

const cut = (s: string, n = 60) => (s.length > n ? `${s.slice(0, n)}…` : s);

export function Schedules({ agent, role }: { agent: string; role: string | undefined }) {
  const { served } = useServes('schedules.list');
  const q = useSchedules();
  const mayAdd = useSelector((s) => s.can('schedule.add'));
  const add = useWrite('schedule.add');
  const edit = useWrite('schedule.edit');
  const remove = useWrite('schedule.remove');
  const [form, setForm] = useState<{ id?: number; cron: string; message: string } | null>(null);
  const [note, setNote] = useState('');
  if (!served) return null;
  const mine = (q.data?.schedules ?? []).filter((s) => s.target === agent);
  const write = role === 'founder' && mayAdd;
  const fail = (e: unknown) => setNote(sentence(classify(e)));
  const save = () => {
    if (!form || !form.cron.trim() || !form.message.trim()) return setNote('A schedule needs a when and a message.');
    setNote('');
    const { id, cron, message } = form;
    setForm(null);
    (id === undefined
      ? add.mutateAsync({ target: agent, cron: cron.trim(), message: message.trim() })
      : edit.mutateAsync({ schedule: id, cron: cron.trim(), message: message.trim() })
    ).catch(fail);
  };
  const drop = (s: Schedule) => {
    if (!confirm(`Remove the schedule "${cut(s.message, 30)}"?`)) return;
    remove.mutateAsync({ schedule: s.id }).catch(fail);
  };
  return (
    <div className="schedules">
      <SectionHead>schedules</SectionHead>
      {q.isError ? (
        <FNote tone="bad" role="alert">
          cannot read the schedules: {sentence(classify(q.error))}{' '}
          <button className="btn" onClick={() => void q.refetch()}>
            Retry
          </button>
        </FNote>
      ) : null}
      {mine.length ? null : <p className="muted">none</p>}
      {mine.map((s) => (
        <div key={s.id} className="schedule flex flex-wrap items-center gap-x-3 gap-y-1 py-1">
          <span>
            {s.id < 0 ? '○ ' : ''}
            {cronWords(s.cron) ?? s.cron}
            {cronWords(s.cron) ? <code className="muted"> {s.cron}</code> : null}
          </span>
          <span className="muted">{s.next_run ? `next ${new Date(s.next_run).toLocaleString()}` : ''}</span>
          <span className="smsg min-w-[12ch] flex-1">{cut(s.message)}</span>
          {write ? (
            <span className="sact">
              <button className="btn" aria-label="Edit schedule" onClick={() => setForm({ id: s.id, cron: s.cron, message: s.message })}>
                ✎
              </button>
              <button className="btn" aria-label="Remove schedule" onClick={() => drop(s)}>
                ✕
              </button>
            </span>
          ) : null}
        </div>
      ))}
      {note ? (
        <FNote tone="bad" role="alert">
          {note}
        </FNote>
      ) : null}
      {write && !form ? (
        <button className="btn" onClick={() => setForm({ cron: '', message: '' })}>
          Add schedule
        </button>
      ) : null}
      {form ? (
        <Sheet role="dialog" aria-label="Schedule">
          <input
            type="text"
            aria-label="When (cron)"
            placeholder="0 9 * * 1-5"
            value={form.cron}
            onChange={(e) => setForm({ ...form, cron: e.target.value })}
          />
          <span className="muted">{cronWords(form.cron) ?? ''}</span>
          <textarea aria-label="Message" rows={2} value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} />
          <SheetActions>
            <button className="btn primary" onClick={save}>
              Save
            </button>
            <button className="btn" onClick={() => setForm(null)}>
              Cancel
            </button>
          </SheetActions>
        </Sheet>
      ) : null}
    </div>
  );
}
