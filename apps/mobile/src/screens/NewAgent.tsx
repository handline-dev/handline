// New agent (founder, docs/pc-app-gaps.md X5): one full page with the form of docs/design/new-agent-worktrees.md §1.2, then the progress of
// the operation it started. One agent.create per tap; the fields stay here, so a failed creation returns to them as they were.
import type { FirstTask } from '../session.ts';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { EFFORTS, EMPTY_AGENT_FIELDS, classify, sentence, useNewAgentForm, useServes } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { NewAgentProgress } from './NewAgentProgress.tsx';
import { Button, PageHeader } from './parts.tsx';

/** Where the page is, kept past a reload: the operation it is watching, or null for the form. */
export interface Pending {
  operation: string | null;
  /** The first task typed before the operation started: what a relaunch still has to send. */
  first?: FirstTask | null;
  save: (operation: string | null, first?: FirstTask | null) => void;
}

function Label(props: { text: string; note?: string }) {
  const c = usePalette();
  return (
    <View>
      <Text style={{ color: c.muted, fontSize: 12 }}>{props.text}</Text>
      {props.note ? <Text style={{ color: c.muted, fontSize: 12 }}>{props.note}</Text> : null}
    </View>
  );
}

function Input(props: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
}) {
  const c = usePalette();
  return (
    <View style={{ gap: space.xs }}>
      <Label text={props.label} />
      <TextInput
        testID={`na-${props.id}`}
        accessibilityLabel={props.label}
        placeholder={props.placeholder}
        placeholderTextColor={c.muted}
        value={props.value}
        onChangeText={props.onChange}
        autoCapitalize="none"
        autoCorrect={false}
        multiline={props.multiline}
        style={{
          color: c.ink,
          borderWidth: 1,
          borderColor: c.line,
          borderRadius: radius.md,
          padding: space.md,
          minHeight: props.multiline ? 88 : 44,
        }}
      />
    </View>
  );
}

/** One of a few values, as a row of chips; a tap on the chosen one chooses it again. */
function Choice(props: {
  id: string;
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  note?: string;
}) {
  const c = usePalette();
  return (
    <View style={{ gap: space.xs }}>
      <Label text={props.label} note={props.note} />
      <View
        accessibilityRole="radiogroup"
        accessibilityLabel={props.label}
        style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}
      >
        {props.options.map((o) => (
          <Pressable
            key={o.value}
            testID={`na-${props.id}-${o.value || 'default'}`}
            accessibilityRole="radio"
            accessibilityState={{ selected: props.value === o.value }}
            onPress={() => props.onChange(o.value)}
            style={{
              borderWidth: 1,
              borderColor: props.value === o.value ? c.ink : c.line,
              borderRadius: radius.pill,
              paddingVertical: space.sm,
              paddingHorizontal: space.md,
            }}
          >
            <Text style={{ color: props.value === o.value ? c.ink : c.muted, fontWeight: props.value === o.value ? '700' : '400' }}>
              {o.label}
            </Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

function Page(props: { onClose: () => void; children: React.ReactNode }) {
  return (
    <ScrollView
      testID="new-agent"
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: 120 }}
    >
      <PageHeader bare title="New agent" back={{ label: 'Agents', onPress: props.onClose, testID: 'na-back' }} />
      {props.children}
    </ScrollView>
  );
}

export function NewAgent({ pending, onClose, onAgent }: { pending: Pending; onClose: () => void; onAgent: (name: string) => void }) {
  const c = usePalette();
  const founder = useStore().client.hello?.role === 'founder';
  const { served } = useServes('agent.create');
  const { create, agents, f, setF, set, list, prefix, ready, send } = useNewAgentForm((operation, made) =>
    pending.save(operation, made.task.trim() ? { task: made.task, box: made.box } : null),
  );
  if (!founder || !served)
    return (
      <Page onClose={onClose}>
        <Text testID="na-blocked" style={{ color: c.ink }}>
          {founder ? 'This daemon cannot create agents.' : 'New agents are for the founder.'}
        </Text>
      </Page>
    );
  if (pending.operation)
    return (
      <NewAgentProgress
        operation={pending.operation}
        fields={f.task ? f : { ...f, ...pending.first }}
        onSent={() => pending.save(pending.operation)}
        onClose={() => (pending.save(null), onClose())}
        onAgent={(name) => (pending.save(null), onAgent(name))}
        onForm={() => pending.save(null)}
        onAnother={() => (setF(EMPTY_AGENT_FIELDS), pending.save(null))}
      />
    );
  const refused = create.isError ? classify(create.error) : null;
  return (
    <Page onClose={onClose}>
      {refused ? (
        <Text testID="na-error" accessibilityRole="alert" style={{ color: c.block }}>
          {sentence(refused)}
        </Text>
      ) : null}
      <Input id="name" label={prefix ? `Name (after ${prefix})` : 'Name'} value={f.name} onChange={(v) => set('name', v)} />
      <Label text="Plain text: no /, no leading ." />
      <Input id="role" label="Role" value={f.role} onChange={(v) => set('role', v)} />
      <Choice
        id="repo"
        label="Repo"
        value={f.repo}
        options={list.map((r) => ({ value: r.path, label: r.name }))}
        note={list.length ? undefined : 'The daemon knows no repo.'}
        onChange={(v) => setF((was) => ({ ...was, repo: v, base: list.find((r) => r.path === v)?.default_branch ?? '' }))}
      />
      <Input id="base" label="Base" value={f.base} onChange={(v) => set('base', v)} />
      <Input id="model" label="Model" placeholder="default" value={f.model} onChange={(v) => set('model', v)} />
      <Choice
        id="effort"
        label="Effort"
        value={f.effort}
        options={[{ value: '', label: 'default' }, ...EFFORTS.map((e) => ({ value: e, label: e }))]}
        onChange={(v) => set('effort', v)}
      />
      <Choice
        id="parent"
        label="Parent"
        value={f.parent}
        options={[{ value: '', label: 'none' }, ...agents.map((a) => ({ value: a.name, label: a.name }))]}
        onChange={(v) => set('parent', v)}
      />
      <Label text="Account: auto" note="A new agent's account is chosen when it starts; none can be named yet." />
      <Input id="task" label="First task (optional)" value={f.task} onChange={(v) => set('task', v)} multiline />
      {f.task.trim() ? <Input id="box" label="Time box" value={f.box} onChange={(v) => set('box', v)} /> : null}
      <Button testID="na-create" kind="yes" label="Create" disabled={!ready || create.isPending} onPress={send} />
    </Page>
  );
}
