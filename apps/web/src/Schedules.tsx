// The agent's schedules: founder-only writes (schedule.add, edit, remove), authored: the line shows at once and reverts on a refusal.
import { cronWords, ellipsis } from '@handline/app-core';
import { classify, sentence, useScheduleEditor, useSchedules, useServes } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import type { Schedule } from '@handline/client';
import { Button, FNote, Icon, SectionHead, Sheet, SheetActions } from '#src/ui/index.ts';

export function Schedules({ agent, role }: { agent: string; role: string | undefined }) {
  const { served } = useServes('schedules.list');
  const q = useSchedules();
  const mayAdd = useSelector((s) => s.can('schedule.add'));
  const { form, setForm, note, save, drop } = useScheduleEditor(agent);
  if (!served) return null;
  const mine = (q.data?.schedules ?? []).filter((s) => s.target === agent);
  const write = role === 'founder' && mayAdd;
  const ask = (s: Schedule) => {
    if (confirm(`Remove the schedule "${ellipsis(s.message, 30)}"?`)) drop(s.id);
  };
  return (
    <div className="schedules">
      <SectionHead>schedules</SectionHead>
      {q.isError ? (
        <FNote tone="bad" role="alert">
          cannot read the schedules: {sentence(classify(q.error))}{' '}
          <Button variant="outline" size="sm" label="Retry" onClick={() => void q.refetch()} />
        </FNote>
      ) : null}
      {mine.length ? null : <p className="muted">none</p>}
      {mine.map((s) => (
        <div key={s.id} className="schedule flex flex-wrap items-center gap-x-3 gap-y-1 py-1">
          <span>
            {s.id < 0 ? <Icon name="unsaved" size={12} /> : null} {cronWords(s.cron) ?? s.cron}
            {cronWords(s.cron) ? <code className="muted"> {s.cron}</code> : null}
          </span>
          <span className="muted">{s.next_run ? `next ${new Date(s.next_run).toLocaleString()}` : ''}</span>
          <span className="smsg min-w-[12ch] flex-1">{ellipsis(s.message, 60)}</span>
          {write ? (
            <span className="sact">
              <Button
                size="sm"
                variant="outline"
                label="Edit schedule"
                aria-label="Edit schedule"
                icon="edit"
                onClick={() => setForm({ id: s.id, cron: s.cron, message: s.message })}
              />
              <Button
                size="sm"
                variant="outline"
                label="Remove schedule"
                aria-label="Remove schedule"
                icon="close"
                onClick={() => ask(s)}
              />
            </span>
          ) : null}
        </div>
      ))}
      {note ? (
        <FNote tone="bad" role="alert">
          {note}
        </FNote>
      ) : null}
      {write && !form ? <Button label="Add schedule" onClick={() => setForm({ cron: '', message: '' })} /> : null}
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
            <Button variant="solid" label="Save" className="max-sm:min-h-12 max-sm:flex-1" onClick={save} />
            <Button variant="outline" label="Cancel" className="max-sm:min-h-12 max-sm:flex-1" onClick={() => setForm(null)} />
          </SheetActions>
        </Sheet>
      ) : null}
    </div>
  );
}
