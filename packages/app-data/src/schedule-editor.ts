// The schedule form of one agent, for the PC and the phone: the form, the reason a refusal gave, and add, edit and remove writes.
import { useState } from 'react';
import { classify, sentence } from '#src/errors.ts';
import { useWrite } from '#src/write.ts';

export type ScheduleForm = { id?: number; cron: string; message: string };

export function useScheduleEditor(agent: string) {
  const add = useWrite('schedule.add');
  const edit = useWrite('schedule.edit');
  const remove = useWrite('schedule.remove');
  const [form, setForm] = useState<ScheduleForm | null>(null);
  const [note, setNote] = useState('');
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
  const drop = (id: number) => {
    setNote('');
    remove.mutateAsync({ schedule: id }).catch(fail);
  };
  return { form, setForm, note, save, drop, pending: add.isPending || edit.isPending || remove.isPending };
}
