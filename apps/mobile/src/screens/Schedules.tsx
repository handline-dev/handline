// An agent's schedules on the phone (docs/pc-app-gaps.md G6): the list with when, next run and message; for the founder Add, Edit and Remove.
// Authored: a line shows at once and goes back with the daemon's reason when refused. Remove asks on the line before it sends.
import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { cronWords, ellipsis } from '@handline/app-core';
import type { Schedule } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { useOff, useScheduleEditor, useSchedules, useServes } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Button } from '#src/screens/parts.tsx';

export function Schedules({ agent, role }: { agent: string; role: string | undefined }) {
  const c = usePalette();
  const query = useSchedules();
  const { served } = useServes('schedules.list');
  const mayAdd = useSelector((s) => s.can('schedule.add'));
  const whyNot = useOff('schedule.add');
  const { form, setForm, note, save, drop: removeNow, pending } = useScheduleEditor(agent);
  const [asking, setAsking] = useState<number | null>(null);
  const mine = (query.data?.schedules ?? []).filter((s) => s.target === agent);
  const write = role === 'founder' && mayAdd;
  const drop = (s: Schedule) => {
    setAsking(null);
    removeNow(s.id);
  };
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
              <Text style={{ color: c.ink }}>{ellipsis(s.message, 80)}</Text>
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
