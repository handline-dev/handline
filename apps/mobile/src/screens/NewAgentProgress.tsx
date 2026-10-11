// The progress of one agent.create, from the operations topic: the step the daemon sent (none invented), then done or failed. A failed
// creation offers the form back with its fields; a done one sends the first task, if there was one, as a task.new.
import { useEffect, useRef, useState } from 'react';
import { ScrollView, Text, TextInput, View } from 'react-native';
import { classify, sentence, useOperation, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { Skeleton } from '../skeleton.tsx';
import { usePalette } from '../theme.ts';
import type { Fields } from './NewAgent.tsx';
import { Button } from './parts.tsx';

export function NewAgentProgress(props: {
  operation: string;
  fields: Fields;
  onClose: () => void;
  onAgent: (name: string) => void;
  onForm: () => void;
  onAnother: () => void;
}) {
  const c = usePalette();
  const q = useOperation(props.operation);
  const op = q.data;
  const ask = useWrite('task.new');
  const sent = useRef(false);
  const [text, setText] = useState(props.fields.task);
  const agent = op?.agent ?? null;
  const done = op?.state === 'done';
  const send = () => {
    const [title = '', ...rest] = text.trim().split('\n');
    if (!agent || !title) return;
    ask.mutate({ owner: agent, title: title.slice(0, 80), body: rest.join('\n').trim(), time_box: props.fields.box.trim() || '2h' });
  };
  useEffect(() => {
    if (done && !sent.current && text.trim()) {
      sent.current = true;
      send();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the operation turns done
  }, [done]);
  const body = () => {
    if (!op)
      return q.isError ? (
        <View testID="na-error" accessibilityRole="alert" style={{ gap: space.sm }}>
          <Text style={{ color: c.block }}>{sentence(classify(q.error))}</Text>
          <Button label="Retry" onPress={() => void q.refetch()} />
        </View>
      ) : (
        <Skeleton shape="agentDetail" loading label="the new agent" />
      );
    if (op.state === 'running')
      return (
        <View testID="na-progress" accessibilityRole="progressbar" style={{ gap: space.sm }}>
          <Text style={{ color: c.ink, fontWeight: '600' }}>Creating {agent ?? 'the agent'}</Text>
          {op.step ? <Text style={{ color: c.ink }}>{op.step}</Text> : null}
          {op.steps_total ? (
            <>
              <View style={{ height: 6, borderRadius: 3, backgroundColor: c.line }}>
                <View
                  style={{
                    width: `${Math.min(100, (op.steps_done / op.steps_total) * 100)}%`,
                    height: 6,
                    borderRadius: 3,
                    backgroundColor: c.run,
                  }}
                />
              </View>
              <Text style={{ color: c.muted }}>
                step {Math.min(op.steps_done + 1, op.steps_total)} of {op.steps_total}
              </Text>
            </>
          ) : null}
          {op.message ? <Text style={{ color: c.muted }}>{op.message}</Text> : null}
        </View>
      );
    if (op.state === 'failed') {
      const registered = (op.result as { registered?: boolean } | null)?.registered === true;
      return (
        <View testID="na-failed" style={{ gap: space.md }}>
          <Text accessibilityRole="alert" style={{ color: c.block }}>
            {op.message ?? 'Creating the agent failed.'}
          </Text>
          {registered && agent ? <Text style={{ color: c.ink }}>{agent} is registered, not started.</Text> : null}
          {registered && agent ? <Button testID="na-open" label="Open agent" onPress={() => props.onAgent(agent)} /> : null}
          <Button testID="na-edit" kind="yes" label="Edit and try again" onPress={props.onForm} />
        </View>
      );
    }
    return (
      <View testID="na-done" style={{ gap: space.md }}>
        <Text style={{ color: c.ink }}>
          {agent} is running{ask.isError ? ', but the first task was not sent' : ''}.
        </Text>
        {ask.isError ? (
          <>
            <Text accessibilityRole="alert" style={{ color: c.block }}>
              {sentence(classify(ask.error))}
            </Text>
            <TextInput
              testID="na-retask"
              accessibilityLabel="First task"
              value={text}
              onChangeText={setText}
              multiline
              style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 88 }}
            />
            <Button testID="na-retry-task" label="Retry" disabled={ask.isPending} onPress={send} />
          </>
        ) : null}
        {agent ? <Button testID="na-open" kind="yes" label="Open agent" onPress={() => props.onAgent(agent)} /> : null}
        <Button testID="na-another" label="Create another" onPress={props.onAnother} />
      </View>
    );
  };
  return (
    <ScrollView testID="new-agent" contentContainerStyle={{ padding: space.lg, gap: space.md }}>
      <Button testID="na-back" label="‹ Back" onPress={props.onClose} style={{ alignSelf: 'flex-start' }} />
      <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700' }}>New agent</Text>
      {body()}
    </ScrollView>
  );
}
