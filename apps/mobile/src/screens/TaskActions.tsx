// The phone's task verbs, as on the web's Tasks page (docs/pc-app-gaps.md U11): new task, done, reopen, queue, draft, discard, edit, memo, withdraw, cancel. Each is a
// `useWrite`: an authored one shows at once and rolls back with the daemon's reason on a refusal; a verb the daemon does not serve is
// disabled with the reason. The sheets keep what was typed until the daemon accepts.
import { useState, type ReactNode } from 'react';
import { isGate, isPerson, taskRef } from '@handline/app-core';
import { Text, TextInput, View } from 'react-native';
import { useStore } from '@handline/app-react';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useAgents, useOff, usePendingAny, useTaskActions, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { ImageBodyInput } from '#src/screens/ImageBodyInput.tsx';
import { BottomSheet, Button, MenuItem, MenuSheet, PageHeader, Pill } from '#src/screens/parts.tsx';
import { AskAbout } from '#src/screens/TaskAsk.tsx';
import { AssignTask } from '#src/screens/TaskAssign.tsx';
import { MergeTasks, ReleaseTask } from '#src/screens/TaskReleaseMerge.tsx';

const CLOSED = ['done', 'discarded'];

export function Input(props: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  testID: string;
  multiline?: boolean;
  invalid?: boolean;
}) {
  const c = usePalette();
  return (
    <View style={{ gap: space.xs }}>
      <Text style={{ color: c.muted, fontWeight: '600' }}>{props.label}</Text>
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
          borderRadius: radius.card,
          padding: space.md,
          minHeight: props.multiline ? 72 : 44,
        }}
      />
    </View>
  );
}

const PRIORITIES = [
  [1, '1 high'],
  [2, '2'],
  [3, '3 low'],
] as const;

const GROW = { flex: 1 };

/** A bottom sheet with a reason field, Submit and Cancel; without onSubmit it is read-only with one Close. */
export function Sheet(props: {
  label: string;
  submit?: string;
  kind?: 'yes' | 'no';
  children?: ReactNode;
  onCancel: () => void;
  onSubmit?: () => void;
  error?: string;
  busy?: boolean;
  /** Prefix of the test ids: `<id>-sheet`, `-error`, `-go`, `-cancel`. */
  id?: string;
}) {
  const c = usePalette();
  const { submit, kind, onSubmit, onCancel, busy, id } = props;
  return (
    <BottomSheet testID={id ? `${id}-sheet` : 'sheet'} padded onClose={onCancel}>
      <Text style={{ color: c.ink, fontWeight: '600', fontSize: 16 }}>{props.label}</Text>
      {props.children}
      {props.error ? (
        <Text testID={id ? `${id}-error` : 'sheet-error'} accessibilityRole="alert" style={{ color: c.block }}>
          {props.error}
        </Text>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        {onSubmit && (
          <Button testID={id ? `${id}-go` : 'sheet-submit'} kind={kind ?? 'yes'} label={submit ?? 'Submit'} disabled={busy} onPress={onSubmit} style={GROW} />
        )}
        <Button testID={id ? `${id}-cancel` : 'sheet-cancel'} label={onSubmit ? 'Cancel' : 'Close'} onPress={onCancel} style={onSubmit ? undefined : GROW} />
      </View>
    </BottomSheet>
  );
}

function ReasonSheet(props: {
  label: string;
  submit: string;
  kind?: 'yes' | 'no';
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
      kind={props.kind}
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

type Ask = null | 'ask' | 'done' | 'reopen' | 'withdraw' | 'cancel' | 'discard' | 'edit' | 'release' | 'merge' | 'assign';

/** The sheets that take the task and a way to close: the verbs that ask for more than a reason. */
const PANELS = { ask: AskAbout, release: ReleaseTask, merge: MergeTasks, assign: AssignTask, edit: EditTask } as const;

/** The verbs of one task; each is shown only when it applies, and disabled with its reason when the daemon does not serve it. */
export function TaskVerbs({ t }: { t: TaskDetail }) {
  const c = usePalette();
  const me = useStore().me;
  const [ask, setAsk] = useState<Ask>(null);
  const [menu, setMenu] = useState<null | 'status' | 'more'>(null);
  const { done, reopen, withdraw, cancel, queue, draft, discard } = useTaskActions();
  const off = {
    done: useOff('task.done'),
    reopen: useOff('task.reopen'),
    withdraw: useOff('task.withdraw'),
    cancel: useOff('task.cancel'),
    queue: useOff('task.queue'),
    draft: useOff('task.draft'),
    discard: useOff('task.discard'),
    edit: useOff('task.edit'),
    ask: useOff('task.ask'),
    release: useOff('task.release'),
    merge: useOff('task.merge'),
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
  const reasons: Partial<Record<Exclude<Ask, null>, Omit<Parameters<typeof ReasonSheet>[0], 'onCancel' | 'onSubmit'> & { send: (text: string) => Promise<unknown> }>> = {
    done: { label: 'done: what came of it?', submit: 'Done', required: true, send: (evidence) => done.mutateAsync({ task: t.id, evidence, reason: true }) },
    reopen: { label: 'reopen: why?', submit: 'Reopen', required: true, send: (note) => reopen.mutateAsync({ task: t.id, note }) },
    cancel: {
      label: running ? `stop ${taskRef(t.id)}? why (optional):` : `cancel ${taskRef(t.id)}? why (optional):`,
      submit: running ? 'Stop it' : 'Cancel task',
      kind: 'no',
      required: false,
      send: (reason) => cancel.mutateAsync({ task: t.id, reason: reason || undefined }),
    },
    discard: {
      label: running ? `stop ${taskRef(t.id)} and discard it? why:` : `discard ${taskRef(t.id)}? why:`,
      submit: 'Discard',
      kind: 'no',
      required: true,
      send: (reason) => discard.mutateAsync({ task: t.id, reason }),
    },
  };
  const reason = ask ? reasons[ask] : undefined;
  const Panel = ask ? PANELS[ask as keyof typeof PANELS] : undefined;
  const verb = (id: string, label: string, why: string | undefined, to: Ask) => (
    <MenuItem key={id} testID={`verb-${id}`} label={label} disabled={!!why || ask !== null} onPress={() => {
      setMenu(null);
      setAsk(to);
    }} />
  );
  return (
    <View style={{ gap: space.md }}>
      <View accessibilityRole="toolbar" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        <Pill testID="status-menu" on={menu === 'status'} label={`${t.state} ▾`} onPress={() => (setAsk(ask === 'assign' ? null : ask), setMenu(menu === 'status' ? null : 'status'))} />
        {open && !isGate(t) && <Pill testID="assignee-menu" on={ask === 'assign'} label={`→ ${t.to} ▾`} onPress={() => {
            setMenu(null);
            setAsk(ask === 'assign' ? null : 'assign');
          }} />}
        <Pill testID="more-menu" on={menu === 'more'} label="More ▾" onPress={() => (setAsk(ask === 'assign' ? null : ask), setMenu(menu === 'more' ? null : 'more'))} />
      </View>
      {menu !== null && (
        <MenuSheet testID={`${menu}-sheet`} onClose={() => setMenu(null)}>
          {menu === 'status' ? (
            <>
              {open && verb('done', isPerson(t.from, me) ? 'Send for review' : 'Done', off.done, 'done')}
              {(t.state === 'discarded' || (t.state === 'done' && mine)) && verb('reopen', 'Reopen', off.reopen, 'reopen')}
              {t.state === 'draft' && (
                <MenuItem
                  testID="verb-queue"
                  label="Queue"
                  disabled={!!off.queue || ask !== null || queue.isPending}
                  onPress={() => {
                    setMenu(null);
                    void queue.mutateAsync({ tasks: [t.id] }).catch(() => {}); // the write's own rollback shows the reason
                  }}
                />
              )}
              {t.state === 'queued' && (
                <MenuItem
                  testID="verb-draft"
                  label="Back to draft"
                  disabled={!!off.draft || ask !== null || draft.isPending}
                  onPress={() => {
                    setMenu(null);
                    void draft.mutateAsync({ tasks: [t.id] }).catch(() => {}); // the write's own rollback shows the reason
                  }}
                />
              )}
              {running && verb('release', 'Release', off.release, 'release')}
            </>
          ) : (
            <>
              {startable && mine && verb('withdraw', 'Withdraw', off.withdraw, 'withdraw')}
              {open && verb('cancel', running ? 'Stop' : 'Cancel', off.cancel, 'cancel')}
              {verb('ask', 'Ask', off.ask, 'ask')}
              {open && verb('merge', 'Merge…', off.merge, 'merge')}
              {open && verb('edit', 'Edit', off.edit, 'edit')}
              {open && verb('discard', 'Discard', off.discard, 'discard')}
            </>
          )}
        </MenuSheet>
      )}
      {Object.values(off).some(Boolean) && <Text style={{ color: c.muted, fontSize: font.size.label }}>{Object.values(off).find(Boolean)}</Text>}
      {reason && <ReasonSheet {...reason} onCancel={() => setAsk(null)} onSubmit={(text) => settle(reason.send(text))} />}
      {ask === 'withdraw' && (
        <Sheet
          label={`withdraw ${taskRef(t.id)}?`}
          submit="Withdraw"
          kind="no"
          onCancel={() => setAsk(null)}
          onSubmit={() => void settle(withdraw.mutateAsync({ task: t.id }))}
        >
          <Text style={{ color: c.muted }}>Takes the task back before anyone has started it.</Text>
        </Sheet>
      )}
      {Panel && <Panel t={t} onClose={() => setAsk(null)} />}
    </View>
  );
}

const TASK_WRITES = [
  'task.done', 'task.reopen', 'task.withdraw', 'task.cancel', 'task.queue', 'task.draft', 'task.discard', 'task.edit', 'task.ask', 'task.release', 'task.merge',
  'task.comment.add', 'task.comment.edit', 'task.comment.delete',
] as const;

/** Whether a write for this task is on its way: the detail shows a quiet mark until the daemon settles it. */
export function useTaskPending(id: number): boolean {
  const mine = (v: { task?: unknown; tasks?: unknown }) => v.task === id || (Array.isArray(v.tasks) && v.tasks.includes(id));
  return usePendingAny(TASK_WRITES).some(mine);
}

interface TaskDraft {
  owner: string;
  title: string;
  body: string;
  box: string;
  priority: 1 | 2 | 3;
}

/** The fields a new task and an edited one share: owner, title, body, time box, priority. `id` prefixes the test ids. */
function TaskFields(p: { id: 'nt' | 'et'; owners: string[]; draft: TaskDraft; change: (patch: Partial<TaskDraft>) => void }) {
  const c = usePalette();
  const { id, draft, change } = p;
  return (
    <>
      <Text style={{ color: c.muted, fontWeight: '600' }}>Owner</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        {p.owners.map((a) => (
          <Pill role="radio" key={a} on={draft.owner === a} label={a} onPress={() => change({ owner: a })} testID={`${id}-owner-${a}`} />
        ))}
      </View>
      <Input testID={`${id}-title`} label="Title" value={draft.title} onChange={(title) => change({ title })} />
      <ImageBodyInput testID={`${id}-body`} label="Body" value={draft.body} onChange={(body) => change({ body })} />
      <Input testID={`${id}-box`} label="Time box" value={draft.box} onChange={(box) => change({ box })} />
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        {PRIORITIES.map(([n, l]) => (
          <Pill role="radio" key={n} on={draft.priority === n} label={l} onPress={() => change({ priority: n })} testID={`${id}-priority-${n}`} />
        ))}
      </View>
    </>
  );
}

/** New task: owner, title, body, time box, priority. The row shows in the list at once; a refusal leaves the text here with the reason. */
export function NewTask({ onClose }: { onClose: () => void }) {
  const c = usePalette();
  const ask = useWrite('task.new');
  const why = useOff('task.new');
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  const [draft, setDraft] = useState<TaskDraft>({ owner: '', title: '', body: '', box: '2h', priority: 2 });
  const change = (patch: Partial<TaskDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const [missing, setMissing] = useState('');
  const send = () => {
    const { owner, title, body, box, priority } = draft;
    if (!owner) return setMissing('Pick who it is for.');
    if (!title.trim()) return setMissing('A task needs a title.');
    setMissing('');
    ask.mutateAsync({ owner, title: title.trim(), body, time_box: box.trim() || '2h', priority }).then(onClose, () => {});
  };
  const said = missing || (ask.isError ? sentence(classify(ask.error)) : '');
  return (
    <View testID="new-task" style={{ gap: space.md, padding: space.lg }}>
      <PageHeader bare title="New task" />
      <TaskFields id="nt" owners={agents} draft={draft} change={change} />
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

/** Edit: only what changed is sent. The sheet stays open with the text when the daemon refuses. */
function EditTask({ t, onClose }: { t: TaskDetail; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  const [draft, setDraft] = useState<TaskDraft>({ owner: t.to, title: t.title, body: t.body ?? '', box: t.time_box ?? '', priority: t.priority ?? 2 });
  const change = (patch: Partial<TaskDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const [missing, setMissing] = useState('');
  const save = () => {
    const { owner, title, body, box, priority } = draft;
    if (!title.trim()) return setMissing('A task needs a title.');
    setMissing('');
    const change = {
      ...(title.trim() !== t.title ? { title: title.trim() } : {}),
      ...(body !== (t.body ?? '') ? { body } : {}),
      ...(owner !== t.to ? { owner } : {}),
      ...(box.trim() && box.trim() !== t.time_box ? { time_box: box.trim() } : {}),
      ...(priority !== (t.priority ?? 2) ? { priority } : {}),
    };
    if (!Object.keys(change).length) return onClose();
    edit.mutateAsync({ task: t.id, ...change }).then(onClose, () => {});
  };
  return (
    <Sheet
      label={`edit ${taskRef(t.id)}`}
      submit="Save"
      busy={edit.isPending}
      onCancel={onClose}
      onSubmit={save}
      error={missing || (edit.isError ? sentence(classify(edit.error)) : undefined)}
    >
      <TaskFields id="et" owners={[...new Set([t.to, ...agents])]} draft={draft} change={change} />
    </Sheet>
  );
}
