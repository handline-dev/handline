import { useState } from 'react';
import { FlatList, Pressable, Text, View } from 'react-native';
import type { AgentView as Agent } from '@handline/client';
import { useAgents, useHrCount, useMyTurn, useNav, useOpenTasks, useServes } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { font, rowHeight, space } from '@handline/tokens';
import {
  ago,
  rowWord,
  queuedBehind,
  waitingTasks,
  taskRef,
  statusText,
  chipCounts,
  chipChanges,
  chipOf,
  turnAgents,
  chipList,
  moveInOrder,
  rowName,
  siblingsOf,
  teamOf,
  type NavChip,
} from '@handline/app-core';
import { KIND } from '#src/screens/NeedsYouCard.tsx';
import { usePalette } from '#src/theme.ts';
import { StateMark } from '#src/screens/StateMark.tsx';
import { Icon } from '#src/screens/Icon.tsx';
import { Button, EmptyState, MenuItem, MenuSheet, PageHeader } from '#src/screens/parts.tsx';
import { TeamButton } from '#src/screens/TeamEntry.tsx';
import { Loadable } from '#src/screens/Loadable.tsx';

type Item = { key: string; head: string } | { key: string; agent: Agent; depth: number; label: string; head?: undefined };

const CHIPS: { id: NavChip; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'unread', label: 'Unread' },
  { id: 'needs', label: 'Requests' },
];

export function AgentList(props: { onPage: (page: 'new' | 'settings' | 'team' | 'requests') => void }) {
  const c = usePalette();
  const store = useStore();
  const query = useAgents();
  const { served } = useServes('agents.list');
  const agents = query.data?.agents ?? [];
  // An older daemon sets has_dialog without needs_you.
  const asks = (a: Agent) => !!a.needs_you || a.has_dialog;
  const { nav, ready, change, togglePin } = useNav();
  const turn = useMyTurn().cards;
  const chip = chipOf(nav);
  const { pinned, rows } = chipList(agents, nav, turnAgents(turn));
  const counts = chipCounts(agents);
  const names = agents.map((a) => a.name);
  const tree = nav.layout !== 'flat';
  const items: Item[] = [];
  if (pinned.length)
    items.push(
      { key: 'h-pinned', head: 'Pinned' },
      ...pinned.map((agent) => ({ key: `p-${agent.name}`, agent, depth: 0, label: agent.name })),
    );
  let team = '';
  for (const r of rows) {
    const t = teamOf(r.agent.name, names);
    if (tree && t !== team) items.push({ key: `h-${t}`, head: t });
    team = t;
    items.push({
      key: r.agent.name,
      agent: r.agent,
      depth: tree ? r.depth : 0,
      label: tree ? rowName(r.agent.name, r.depth, t) : r.agent.name,
    });
  }
  const [target, setTarget] = useState<Agent | null>(null);
  const [queueOf, setQueueOf] = useState<Agent | null>(null);
  const founder = store.client.hello?.role === 'founder';
  const create = useServes('agent.create').served;
  const hr = useHrCount();
  const requests = useServes('requests.list').served;
  const [menu, setMenu] = useState(false);
  const go = (page: 'settings' | 'team' | 'requests') => (setMenu(false), props.onPage(page));
  return (
    <View style={{ flex: 1 }}>
      <PageHeader
        title="Agents"
        actions={
          <>
            {founder && create ? (
              <Button
                testID="new-agent-open"
                label=""
                icon="plus"
                accessibilityLabel="New agent"
                onPress={() => props.onPage('new')}
                style={{ paddingVertical: space.sm }}
              />
            ) : null}
            <Button
              testID="agents-menu"
              label={hr ? '●' : ''}
              icon="menu"
              accessibilityLabel="More"
              onPress={() => setMenu(!menu)}
              style={{ paddingVertical: space.sm }}
            />
          </>
        }
      />
      {menu && (
        <MenuSheet testID="agents-menu-list" title="Agents" onClose={() => setMenu(false)}>
          <TeamButton onPress={() => go('team')} />
          {requests ? <MenuItem testID="requests-open" label="My requests" icon="tasks" onPress={() => go('requests')} /> : null}
          <MenuItem testID="settings-open" label="Settings" icon="settings" onPress={() => go('settings')} />
        </MenuSheet>
      )}
      {queueOf && <QueuedSheet agent={queueOf} onClose={() => setQueueOf(null)} />}
      {target && (
        <MenuSheet testID="agent-pin-menu" onClose={() => setTarget(null)}>
          {tree && rows.some((r) => r.agent.name === target.name) ? (
            <>
              {(['up', 'down', 'top'] as const).map((way) => {
                const sibs = siblingsOf(rows, target.name);
                const at = sibs.indexOf(target.name);
                const to = way === 'up' ? at - 1 : way === 'down' ? at + 1 : 0;
                return (
                  <MenuItem
                    key={way}
                    testID={`move-${way}`}
                    label={way === 'up' ? 'Move up' : way === 'down' ? 'Move down' : 'Move to top'}
                    icon={way === 'down' ? 'chevron' : 'less'}
                    disabled={!ready || to < 0 || to >= sibs.length || to === at}
                    onPress={() => (change({ order: moveInOrder(nav.order, sibs, target.name, to) }), setTarget(null))}
                  />
                );
              })}
            </>
          ) : null}
          <MenuItem
            testID="pin-toggle"
            label={nav.pins.includes(target.name) ? 'Unpin' : 'Pin'}
            icon="pin"
            disabled={!ready}
            onPress={() => (togglePin(target.name), setTarget(null))}
          />
        </MenuSheet>
      )}
      <Loadable query={query} served={served} isEmpty={agents.length === 0} skeleton="agents" label="agents" empty="No agents.">
        <FlatList
          data={items}
          keyExtractor={(i) => i.key}
          ListHeaderComponent={
            <View
              style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.lg, paddingVertical: space.sm }}
            >
              {CHIPS.map((x) => {
                const on = chip === x.id;
                return (
                  <Pressable
                    key={x.id}
                    testID={`chip-${x.id}`}
                    accessibilityRole="button"
                    accessibilityState={{ selected: on }}
                    disabled={!ready}
                    onPress={() => change(chipChanges(x.id))}
                    style={{
                      paddingHorizontal: space.md,
                      paddingVertical: space.xs,
                      borderRadius: 999,
                      backgroundColor: on ? c.panel : 'transparent',
                      borderWidth: 1,
                      borderColor: on ? c.accent : c.line,
                    }}
                  >
                    <Text style={{ color: on ? c.accent : c.muted, fontSize: font.size.md, fontWeight: on ? '700' : '500' }}>
                      {x.label}
                      {x.id === 'unread' ? ` ${counts[x.id]}` : ''}
                    </Text>
                  </Pressable>
                );
              })}
              <View style={{ flex: 1 }} />
              <Pressable
                testID="layout-toggle"
                accessibilityRole="button"
                accessibilityLabel={tree ? 'Show as a flat list' : 'Show as a tree'}
                disabled={!ready}
                onPress={() => change({ layout: tree ? 'flat' : 'tree' })}
                hitSlop={8}
              >
                <Icon name={tree ? 'viewTree' : 'viewList'} decorative size={20} color={c.ink} />
              </Pressable>
            </View>
          }
          ListEmptyComponent={<EmptyState icon="search" text="No agents match." testID="no-match" />}
          renderItem={({ item }) => {
            if (item.head !== undefined)
              return (
                <Text
                  style={{
                    color: c.muted,
                    fontSize: font.size.sm,
                    paddingHorizontal: space.lg,
                    paddingTop: space.md,
                    paddingBottom: space.xs,
                  }}
                >
                  {item.head}
                </Text>
              );
            const a = item.agent;
            const need = a.needs_you ? (a.needs_you.text ?? KIND[a.needs_you.kind]) : '';
            const doing =
              need || a.task?.title || (a.state === 'running' && !a.handoff ? a.doing?.text : '') || statusText(a) || a.state_detail || '';
            const word = asks(a) ? 'asks' : rowWord(a);
            const queued = queuedBehind(a) || 0;
            const age = a.needs_you ? a.needs_you.since : (a.status?.since ?? a.state_since);
            return (
              <Pressable
                testID={`agent-${a.name}`}
                onPress={() => void store.openAgent(a.name)}
                onLongPress={() => setTarget(a)}
                style={({ pressed }) => ({
                  backgroundColor: pressed ? c.press : undefined,
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: space.md,
                  minHeight: rowHeight.agent, // the skeleton row's height; a larger font grows the row
                  paddingVertical: space.sm,
                  paddingLeft: space.lg + item.depth * space.lg,
                  paddingRight: space.lg,
                  borderTopWidth: 1,
                  borderTopColor: c.line,
                })}
              >
                <StateMark agent={a} />
                <View style={{ flex: 1 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
                    <Text numberOfLines={1} style={{ color: c.ink, fontWeight: '600', flexShrink: 1 }}>
                      {item.label}
                    </Text>
                    {queued ? (
                      <Pressable
                        testID={`queued-${a.name}`}
                        accessibilityRole="button"
                        accessibilityLabel={`${queued} waiting behind the current task`}
                        onPress={() => setQueueOf(a)}
                        hitSlop={8}
                        style={{ flexDirection: 'row', alignItems: 'center', gap: 2 }}
                      >
                        <Icon name="tasks" size={11} color={c.muted} decorative />
                        <Text style={{ color: c.muted, fontSize: font.size.xs }}>{queued}</Text>
                      </Pressable>
                    ) : null}
                  </View>
                  <Text
                    numberOfLines={1}
                    testID={need ? `needs-${a.name}` : undefined}
                    style={{ color: need ? c.wait : c.muted, fontSize: font.size.sm }}
                  >
                    {doing || ' '}
                  </Text>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <Text style={{ color: need ? c.wait : a.block ? c.block : c.muted, fontSize: font.size.sm }}>{word}</Text>
                  {age ? <Text style={{ color: c.muted, fontSize: font.size.xs }}>{ago(age)}</Text> : null}
                </View>
              </Pressable>
            );
          }}
        />
      </Loadable>
    </View>
  );
}

/** The tasks waiting behind an agent's current one, as plain lines. */
function QueuedSheet(props: { agent: Agent; onClose: () => void }) {
  const c = usePalette();
  const waiting = waitingTasks(props.agent, useOpenTasks(props.agent.name, true).data?.items ?? []);
  const n = queuedBehind(props.agent) || 0;
  return (
    <MenuSheet testID="queued-sheet" onClose={props.onClose}>
      <View style={{ paddingHorizontal: space.lg, paddingVertical: space.sm, gap: space.sm }}>
        <Text style={{ color: c.ink, fontWeight: '600' }}>
          {n} waiting behind {props.agent.task ? taskRef(props.agent.task.id) : 'nothing'} on {props.agent.name}
        </Text>
        {waiting.map((t) => (
          <Text key={t.id} testID={`queued-task-${t.id}`} style={{ color: c.muted }}>
            {taskRef(t.id)} {t.title}
          </Text>
        ))}
      </View>
    </MenuSheet>
  );
}
