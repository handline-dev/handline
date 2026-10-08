// An agent's schedules on the phone (docs/pc-app-gaps.md G6): the list with when, next run and message; for the founder Add, Edit and Remove.
// Authored: a line shows at once and goes back with the daemon's reason when refused. Remove asks on the line before it sends.
import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { cronWords } from '@handline/app-core';
import type { Schedule } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { classify, sentence, useSchedules, useServes, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Button } from './parts.tsx';
import { useWhyNot } from './TaskActions.tsx';

const cut = (s: string, n = 80) => (s.length > n ? `${s.slice(0, n)}…` : s);

type Form = { id?: number; cron: string; message: string };

export function Schedules({ agent, role }: { agent: string; role: string | undefined }) {
  const c = usePalette();
  const query = useSchedules();
  const { served } = useServes('schedules.list');
  const mayAdd = useSelector((s) => s.can('schedule.add'));
  const add = useWrite('schedule.add');
  const edit = useWrite('schedule.edit');
  const remove = useWrite('schedule.remove');
  const whyNot = useWhyNot('schedule.add');
  const [form, setForm] = useState<Form | null>(null);
  const [asking, setAsking] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const mine = (query.data?.schedules ?? []).filter((s) => s.target === agent);
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
    setAsking(null);
    setNote('');
    remove.mutateAsync({ schedule: s.id }).catch(fail);
  };
  const pending = add.isPending || edit.isPending || remove.isPending;
  const input = { color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 44 };
  return (
    <View testID="schedules" style={{ gap: space.sm }}>
      <Text style={{ color: c.ink, fontSize: 16, fontWeight: '700' }}>Schedules</Text>
      <Loadable
        query={query}
        served={served}
        isEmpty={mine.length === 0}
        skeleton="settings"
        label="schedules"
        empty="No schedules for this agent."
      >
        <View>
          {mine.map((s) => (
            <View
              key={s.id}
              testID={`schedule-${s.id}`}
              style={{ gap: 2, paddingVertical: space.sm, borderTopWidth: 1, borderTopColor: c.line }}
            >
              <Text style={{ color: c.ink, fontWeight: '600' }}>
                {s.id < 0 ? 'sending: ' : ''}
                {cronWords(s.cron) ?? s.cron}
              </Text>
              {cronWords(s.cron) ? <Text style={{ color: c.muted, fontSize: 12 }}>{s.cron}</Text> : null}
              {s.next_run ? <Text style={{ color: c.muted, fontSize: 12 }}>next {new Date(s.next_run).toLocaleString()}</Text> : null}
              <Text style={{ color: c.ink }}>{cut(s.message)}</Text>
              {write && asking === s.id ? (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                  <Text style={{ color: c.ink, flex: 1 }}>Remove this schedule?</Text>
                  <Button testID="schedule-remove-yes" label="Remove" kind="no" onPress={() => drop(s)} />
                  <Button testID="schedule-remove-no" label="Keep" onPress={() => setAsking(null)} />
                </View>
              ) : write ? (
                <View style={{ flexDirection: 'row', gap: space.sm }}>
                  <Button testID="schedule-edit" label="Edit" onPress={() => setForm({ id: s.id, cron: s.cron, message: s.message })} />
                  <Button testID="schedule-remove" label="Remove" onPress={() => setAsking(s.id)} />
                </View>
              ) : null}
            </View>
          ))}
        </View>
      </Loadable>
      {pending ? (
        <Text testID="schedules-pending" style={{ color: c.muted, fontSize: 12 }}>
          saving…
        </Text>
      ) : null}
      {note ? (
        <Text testID="schedules-note" accessibilityRole="alert" style={{ color: c.block, fontSize: 12 }}>
          not saved: {note}
        </Text>
      ) : null}
      {served && !write ? <Text style={{ color: c.muted, fontSize: 12 }}>Only the founder changes schedules.</Text> : null}
      {served && write && !form ? (
        <Button testID="schedule-new" label="Add schedule" disabled={!!whyNot} onPress={() => setForm({ cron: '', message: '' })} />
      ) : null}
      {form ? (
        <View testID="schedule-form" style={{ gap: space.sm }}>
          <TextInput
            testID="schedule-cron"
            accessibilityLabel="When (cron)"
            placeholder="0 9 * * 1-5"
            placeholderTextColor={c.muted}
            autoCapitalize="none"
            autoCorrect={false}
            value={form.cron}
            onChangeText={(cron) => setForm({ ...form, cron })}
            style={input}
          />
          <Text style={{ color: c.muted, fontSize: 12 }}>{cronWords(form.cron) ?? ''}</Text>
          <TextInput
            testID="schedule-message"
            accessibilityLabel="Message"
            placeholder="Message"
            placeholderTextColor={c.muted}
            multiline
            value={form.message}
            onChangeText={(message) => setForm({ ...form, message })}
            style={{ ...input, minHeight: 72 }}
          />
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Button testID="schedule-save" label="Save" kind="yes" onPress={save} />
            <Button testID="schedule-cancel" label="Cancel" onPress={() => setForm(null)} />
          </View>
        </View>
      ) : null}
    </View>
  );
}
