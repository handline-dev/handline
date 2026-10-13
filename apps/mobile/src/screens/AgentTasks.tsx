// The Tasks tab of an agent's page: what the agent holds or opened, overdue first, then open, then closed; a tap opens the task page.
import { useState } from 'react';
import { FlatList, Pressable, Text } from 'react-native';
import { agentTasks, CLOSED, dueOf, taskRef } from '@handline/app-core';
import { useAgentTasks, useServes } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Detail } from './Tasks.tsx';

export function AgentTasks({ agent }: { agent: string }) {
  const c = usePalette();
  const { served } = useServes('tasks.list');
  const [open, setOpen] = useState<number | null>(null);
  const { query, items, capped } = useAgentTasks(agent);
  const now = Date.now();
  const rows = agentTasks(items, agent, now);
  if (open !== null) return <Detail id={open} onBack={() => setOpen(null)} />;
  return (
    <Loadable
      query={query}
      served={served}
      isEmpty={rows.length === 0}
      skeleton="tasks"
      label="the tasks"
      empty={`${agent} holds and has opened no tasks.`}
    >
      {capped && (
        <Text testID="tasks-capped" style={{ color: c.muted, padding: space.lg }}>
          Showing the newest 200 on each side; older tasks are not listed.
        </Text>
      )}
      <FlatList
        data={rows}
        keyExtractor={(r) => String(r.task.id)}
        renderItem={({ item: { task: t, role } }) => {
          const due = dueOf(t, now);
          const closed = CLOSED.includes(t.state);
          const meta = [
            taskRef(t.id),
            closed ? `${t.state} (closed)` : t.state,
            role === 'assigned' ? `held by ${t.to}` : t.from !== agent ? `from ${t.from}` : null,
            due && (due.over ? `⚠ ${due.text}` : due.text),
          ].filter(Boolean);
          return (
            <Pressable
              testID={`agent-task-${t.id}`}
              onPress={() => setOpen(t.id)}
              style={{
                minHeight: 48,
                justifyContent: 'center',
                paddingHorizontal: space.lg,
                paddingVertical: space.sm,
                borderTopWidth: 1,
                borderTopColor: c.line,
                borderLeftWidth: due?.over ? 4 : 0,
                borderLeftColor: c.block,
                opacity: closed ? 0.6 : 1,
              }}
            >
              <Text numberOfLines={1} style={{ color: c.ink, fontWeight: '600' }}>
                {t.title}
              </Text>
              <Text style={{ color: due?.over ? c.block : c.muted, fontSize: 12 }}>{meta.join(' · ')}</Text>
            </Pressable>
          );
        }}
      />
    </Loadable>
  );
}
