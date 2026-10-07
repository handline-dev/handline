// The phone's task verbs, as on the web's Tasks page (docs/pc-app-gaps.md U11): new task, done, reopen, memo, withdraw, cancel. Each is a
// `useWrite`: an authored one shows at once and rolls back with the daemon's reason on a refusal; a verb the daemon does not serve is
// disabled with the reason. The sheets keep what was typed until the daemon accepts.
import { useState, type ReactNode } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { useStore } from '@handline/app-react';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useAgents, usePending, useServes, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { useConnection } from '@handline/app-react';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';

const CLOSED = ['done', 'discarded'];

/** Why a method cannot be used now, or null when it can. */
export function useWhyNot(method: string): string | null {
  const open = useConnection() === 'open';
  const { served } = useServes(method);
  return !open ? 'daemon not connected' : served ? null : `this daemon does not serve ${method}`;
}

function Input(props: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  testID: string;
  multiline?: boolean;
  invalid?: boolean;
}) {
  const c = usePalette();
  return (
    <TextInput
      testID={props.testID}
      accessibilityLabel={props.label}
      placeholder={props.label}
      placeholderTextColor={c.muted}
      value={props.value}
      onChangeText={props.onChange}
      multiline={props.multiline}
      style={{
        color: c.ink,
        borderWidth: 1,
        borderColor: props.invalid ? c.block : c.line,
        borderRadius: radius.md,
        padding: space.md,
        minHeight: props.multiline ? 72 : 44,
      }}
    />
  );
}

/** A small panel under the verbs: names the thing, takes a reason (required or optional), Submit and Cancel. */
export function Sheet(props: {
  label: string;
  submit: string;
  children?: ReactNode;
  onCancel: () => void;
  onSubmit: () => void;
  error?: string;
  busy?: boolean;
}) {
  const c = usePalette();
  return (
    <View
      testID="sheet"
      style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, backgroundColor: c.panel }}
    >
      <Text style={{ color: c.ink, fontWeight: '600' }}>{props.label}</Text>
      {props.children}
      {props.error ? (
        <Text testID="sheet-error" accessibilityRole="alert" style={{ color: c.block }}>
          {props.error}
        </Text>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button testID="sheet-submit" kind="yes" label={props.submit} disabled={props.busy} onPress={props.onSubmit} style={{ flex: 1 }} />
        <Button testID="sheet-cancel" label="Cancel" onPress={props.onCancel} />
      </View>
    </View>
  );
}

function ReasonSheet(props: {
  label: string;
  submit: string;
  required: boolean;
  onCancel: () => void;
  onSubmit: (reason: string) => Promise<unknown> | void;
}) {
  const [text, setText] = useState('');
  const [empty, setEmpty] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Sheet
      label={props.label}
      submit={props.submit}
      busy={busy}
      onCancel={props.onCancel}
      error={empty ? 'A reason is required.' : undefined}
      onSubmit={() => {
        if (props.required && !text.trim()) return setEmpty(true);
        setBusy(true);
        void Promise.resolve(props.onSubmit(text.trim())).finally(() => setBusy(false));
      }}
    >
      <Input
        testID="sheet-text"
        label="Reason"
        value={text}
        invalid={empty && !text.trim()}
        onChange={(v) => (setText(v), setEmpty(false))}
      />
    </Sheet>
  );
}

type Ask = null | 'done' | 'reopen' | 'withdraw' | 'cancel';

/** The verbs of one task; each is shown only when it applies, and disabled with its reason when the daemon does not serve it. */
export function TaskVerbs({ t }: { t: TaskDetail }) {
  const c = usePalette();
  const me = useStore().me;
  const [ask, setAsk] = useState<Ask>(null);
  const done = useWrite('task.done');
  const reopen = useWrite('task.reopen');
  const withdraw = useWrite('task.withdraw');
  const cancel = useWrite('task.cancel');
  const off = {
    done: useWhyNot('task.done'),
    reopen: useWhyNot('task.reopen'),
    withdraw: useWhyNot('task.withdraw'),
    cancel: useWhyNot('task.cancel'),
  };
  const open = !CLOSED.includes(t.state);
  const mine = t.from === me;
  const running = t.state === 'doing';
  const startable = t.state === 'draft' || t.state === 'queued' || t.state === 'pending';
  // The sheet closes when the daemon accepts; a refusal leaves it open with the text (the write reports the reason).
  const settle = (p: Promise<unknown>) =>
    p.then(
      () => setAsk(null),
      () => {},
    );
  const verb = (id: string, label: string, why: string | null, to: Ask) => (
    <Button key={id} testID={`verb-${id}`} label={label} disabled={!!why || ask !== null} onPress={() => setAsk(to)} />
  );
  return (
    <View style={{ gap: space.md }}>
      <View accessibilityRole="toolbar" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        {open && verb('done', 'Done', off.done, 'done')}
        {t.state === 'done' && mine && verb('reopen', 'Reopen', off.reopen, 'reopen')}
        {startable && mine && verb('withdraw', 'Withdraw', off.withdraw, 'withdraw')}
        {open && verb('cancel', running ? 'Stop' : 'Cancel', off.cancel, 'cancel')}
      </View>
      {Object.values(off).some(Boolean) && <Text style={{ color: c.muted, fontSize: 12 }}>{Object.values(off).find(Boolean)}</Text>}
      {ask === 'done' && (
        <ReasonSheet
          label="done: what came of it?"
          submit="Done"
          required
          onCancel={() => setAsk(null)}
          onSubmit={(evidence) => settle(done.mutateAsync({ task: t.id, evidence, reason: true }))}
        />
      )}
      {ask === 'reopen' && (
        <ReasonSheet
          label="reopen: why?"
          submit="Reopen"
          required
          onCancel={() => setAsk(null)}
          onSubmit={(note) => settle(reopen.mutateAsync({ task: t.id, note }))}
        />
      )}
      {ask === 'withdraw' && (
        <Sheet
          label={`withdraw #${t.id}?`}
          submit="Withdraw"
          onCancel={() => setAsk(null)}
          onSubmit={() => void settle(withdraw.mutateAsync({ task: t.id }))}
        >
          <Text style={{ color: c.muted }}>Takes the task back before anyone has started it.</Text>
        </Sheet>
      )}
      {ask === 'cancel' && (
        <ReasonSheet
          label={running ? `stop #${t.id}? why (optional):` : `cancel #${t.id}? why (optional):`}
          submit={running ? 'Stop it' : 'Cancel task'}
          required={false}
          onCancel={() => setAsk(null)}
          onSubmit={(reason) => settle(cancel.mutateAsync({ task: t.id, reason: reason || undefined }))}
        />
      )}
    </View>
  );
}

/** Whether a write for this task is on its way: the detail shows a quiet mark until the daemon settles it. */
export function useTaskPending(id: number): boolean {
  const mine = (v: { task?: number; tasks?: number[] }) => v.task === id || !!v.tasks?.includes(id);
  return [
    ...usePending('task.done'),
    ...usePending('task.reopen'),
    ...usePending('task.withdraw'),
    ...usePending('task.cancel'),
    ...usePending('task.memo.add'),
  ].some(mine);
}

export function MemoBox({ id }: { id: number }) {
  const c = usePalette();
  const add = useWrite('task.memo.add', String(id));
  const why = useWhyNot('task.memo.add');
  const [text, setText] = useState('');
  const send = () => {
    if (!text.trim()) return;
    // The text stays until the daemon accepts it.
    add.mutateAsync({ task: id, text: text.trim() }).then(
      () => setText(''),
      () => {},
    );
  };
  return (
    <View style={{ gap: space.sm }}>
      <TextInput
        testID="memo-text"
        accessibilityLabel="Memo"
        placeholder="Add a memo"
        placeholderTextColor={c.muted}
        multiline
        editable={!why}
        value={text}
        onChangeText={setText}
        style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 56 }}
      />
      <Button testID="memo-send" label="Add memo" disabled={!!why || !text.trim()} onPress={send} />
      {why && <Text style={{ color: c.muted, fontSize: 12 }}>{why}</Text>}
    </View>
  );
}

const PRIORITIES = [
  [1, '1 high'],
  [2, '2'],
  [3, '3 low'],
] as const;

/** New task: owner, title, body, time box, priority. The row shows in the list at once; a refusal leaves the text here with the reason. */
export function NewTask({ onClose }: { onClose: () => void }) {
  const c = usePalette();
  const ask = useWrite('task.new');
  const why = useWhyNot('task.new');
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  const [owner, setOwner] = useState('');
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [box, setBox] = useState('2h');
  const [priority, setPriority] = useState<1 | 2 | 3>(2);
  const [missing, setMissing] = useState('');
  const send = () => {
    if (!owner) return setMissing('Pick who it is for.');
    if (!title.trim()) return setMissing('A task needs a title.');
    setMissing('');
    ask.mutateAsync({ owner, title: title.trim(), body, time_box: box.trim() || '2h', priority }).then(onClose, () => {});
  };
  const said = missing || (ask.isError ? sentence(classify(ask.error)) : '');
  const chip = (on: boolean, label: string, onPress: () => void, testID: string) => (
    <Pressable
      key={testID}
      testID={testID}
      accessibilityRole="radio"
      accessibilityState={{ selected: on }}
      onPress={onPress}
      style={{
        borderWidth: on ? 2 : 1,
        borderColor: on ? c.ink : c.line,
        borderRadius: radius.pill,
        paddingVertical: space.sm,
        paddingHorizontal: space.md,
      }}
    >
      <Text style={{ color: c.ink, fontWeight: on ? '700' : '400' }}>{on ? `✓ ${label}` : label}</Text>
    </Pressable>
  );
  return (
    <View testID="new-task" style={{ gap: space.md, padding: space.lg }}>
      <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700' }}>New task</Text>
      <Text style={{ color: c.muted, fontWeight: '600' }}>Owner</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        {agents.map((a) => chip(owner === a, a, () => setOwner(a), `nt-owner-${a}`))}
      </View>
      <Input testID="nt-title" label="Title" value={title} onChange={setTitle} />
      <Input testID="nt-body" label="Body" value={body} onChange={setBody} multiline />
      <Input testID="nt-box" label="Time box" value={box} onChange={setBox} />
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        {PRIORITIES.map(([p, l]) => chip(priority === p, l, () => setPriority(p), `nt-priority-${p}`))}
      </View>
      {said ? (
        <Text testID="nt-error" accessibilityRole="alert" style={{ color: c.block }}>
          {said}
        </Text>
      ) : null}
      {why && <Text style={{ color: c.muted }}>{why}</Text>}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button testID="nt-send" kind="yes" label="Ask" disabled={ask.isPending || !!why} onPress={send} style={{ flex: 1 }} />
        <Button testID="nt-cancel" label="Cancel" onPress={onClose} />
      </View>
    </View>
  );
}
