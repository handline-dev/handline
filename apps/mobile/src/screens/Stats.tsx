// The Stats section of the Usage tab (docs/pc-app-gaps.md G10): the window's four counts and how long each agent worked from stats.get, then the usage
// outlook (stats.outlook): the week left as recorded and guessed bars with each account's runway, or the cost per day, with ranges 12 h to 30 d.
// Every number is text too; a bar is never the only way to read it.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { agentTime, costByAgent, runwayWords, span } from '@handline/app-core';
import type { Outlook, Stats as StatsData } from '@handline/client';
import { useOutlook, useServes, useStats } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';

const WINDOWS = ['24h', '7d'] as const;
type Window = (typeof WINDOWS)[number];
type Range = Outlook['range'];

const TILES = [
  ['tasks done', 'tasks_done'],
  ['moves', 'moves'],
  ['handoffs', 'handoffs'],
  ['blocks', 'blocks'],
] as const;

function Choice<T extends string>(props: { label: string; value: T; options: readonly T[]; onChange: (v: T) => void }) {
  const c = usePalette();
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={props.label} style={{ flexDirection: 'row', gap: space.xs, flexWrap: 'wrap' }}>
      {props.options.map((o) => (
        <Pressable
          key={o}
          testID={`${props.label}-${o}`}
          accessibilityRole="radio"
          accessibilityState={{ selected: props.value === o }}
          onPress={() => props.onChange(o)}
          style={{
            paddingVertical: space.sm,
            paddingHorizontal: space.md,
            minHeight: 36,
            justifyContent: 'center',
            borderWidth: 1,
            borderColor: props.value === o ? c.ink : c.line,
            backgroundColor: props.value === o ? c.panel : 'transparent',
            borderRadius: radius.m,
          }}
        >
          <Text style={{ color: props.value === o ? c.ink : c.muted, fontWeight: props.value === o ? '700' : '400' }}>{o}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function Counts({ s }: { s: StatsData }) {
  const c = usePalette();
  const { rows, idle } = agentTime(s.working_seconds);
  const top = rows[0]?.seconds ?? 1;
  return (
    <View style={{ gap: space.lg }}>
      <View testID="stats-tiles" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        {TILES.map(([label, key]) => (
          <View
            key={key}
            testID={`tile-${key}`}
            style={{ flexBasis: '47%', flexGrow: 1, borderWidth: 1, borderColor: c.line, borderRadius: radius.m, padding: space.md }}
          >
            <Text style={{ color: key === 'blocks' && s[key] > 0 ? c.block : c.ink, fontSize: font.size.xl, fontWeight: '700' }}>
              {s[key]}
            </Text>
            <Text style={{ color: c.muted }}>{label}</Text>
          </View>
        ))}
      </View>
      <View testID="agent-time" style={{ gap: space.sm }}>
        <Text style={{ color: c.ink, fontWeight: '600' }}>Agent time</Text>
        {rows.length ? (
          rows.map((r) => (
            <View key={r.agent} testID={`time-${r.agent}`} style={{ gap: 2 }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Text style={{ color: c.ink }}>{r.agent}</Text>
                <Text style={{ color: c.muted }}>{span(r.seconds * 1000)}</Text>
              </View>
              <View style={{ height: 6, borderRadius: 3, backgroundColor: c.line }}>
                <View style={{ width: `${(r.seconds / top) * 100}%`, height: 6, borderRadius: 3, backgroundColor: c.run }} />
              </View>
            </View>
          ))
        ) : (
          <Text style={{ color: c.muted }}>{NOTHING}</Text>
        )}
        {idle ? (
          <Text style={{ color: c.muted }}>
            {idle} idle {idle === 1 ? 'agent' : 'agents'}
          </Text>
        ) : null}
      </View>
    </View>
  );
}

/** The week left over time: one bar per step, solid for what was recorded and hollow for the guess. */
function Left({ o }: { o: Outlook }) {
  const c = usePalette();
  const pts = [...o.past.map((p) => ({ ...p, guess: false })), ...o.future.map((p) => ({ ...p, guess: true }))].filter(
    (p) => p.left_pct !== null,
  );
  return (
    <View style={{ gap: space.sm }}>
      <View
        testID="outlook-left"
        accessibilityLabel={`Week left across ${o.accounts} accounts, recorded and guessed`}
        style={{ flexDirection: 'row', alignItems: 'flex-end', height: 120, gap: 2 }}
      >
        {pts.map((p) => (
          <View
            key={p.at}
            style={{
              flex: 1,
              height: `${Math.max(2, p.left_pct!)}%`,
              backgroundColor: p.guess ? 'transparent' : c.run,
              borderWidth: 1,
              borderColor: c.run,
              borderStyle: p.guess ? 'dashed' : 'solid',
            }}
          />
        ))}
      </View>
      <Text style={{ color: c.muted, fontSize: font.size.sm }}>solid: recorded, dashed: guess. Now is where they change.</Text>
      {o.runway.map((r) => (
        <Text key={r.account} testID={`runway-${r.account}`} style={{ color: c.ink }}>
          {r.account} <Text style={{ color: c.muted }}>{runwayWords(r)}</Text>
        </Text>
      ))}
    </View>
  );
}

/** The cost per day as a row of bars with the day's total under each, and each agent's cost and share below. */
function Cost({ o }: { o: Outlook }) {
  const c = usePalette();
  const { agents, sums, all } = costByAgent(o);
  const total = (d: Outlook['cost_per_day'][number]) => Object.values(d.agents).reduce((a, b) => a + b, 0);
  const top = Math.max(1, ...o.cost_per_day.map(total));
  return (
    <View style={{ gap: space.sm }}>
      <View testID="outlook-cost" style={{ flexDirection: 'row', alignItems: 'flex-end', height: 120, gap: 2 }}>
        {o.cost_per_day.map((d) => (
          <View
            key={d.day}
            testID={`cost-${d.day}`}
            accessibilityLabel={`${d.day}: $${total(d).toFixed(2)}`}
            style={{ flex: 1, height: '100%', justifyContent: 'flex-end' }}
          >
            <View style={{ height: `${(total(d) / top) * 100}%`, backgroundColor: c.run }} />
          </View>
        ))}
      </View>
      <Text style={{ color: c.muted, fontSize: font.size.sm }}>
        {o.cost_per_day[0]?.day} to {o.cost_per_day.at(-1)?.day}, most {`$${top.toFixed(2)}`} a day
      </Text>
      {agents.map((a, i) => (
        <View key={a} testID={`cost-agent-${a}`} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          <Text style={{ color: c.ink }}>{a}</Text>
          <Text style={{ color: c.muted }}>
            ${sums[i]!.toFixed(2)} · {Math.round((sums[i]! / (all || 1)) * 100)}%
          </Text>
        </View>
      ))}
    </View>
  );
}

function OutlookSection({ range }: { range: Range }) {
  const c = usePalette();
  const [view, setView] = useState<'left' | 'cost'>('left');
  const query = useOutlook(range);
  const { served } = useServes('stats.outlook');
  const o = query.data;
  return (
    <View testID="outlook" style={{ gap: space.md }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>Outlook</Text>
      <Choice label="view" value={view} options={['left', 'cost'] as const} onChange={setView} />
      <Loadable
        query={query}
        served={served}
        isEmpty={!!o && !o.past.length && !o.cost_per_day.length}
        skeleton="stats"
        label="the outlook"
        empty={NOTHING}
      >
        {o ? view === 'left' ? <Left o={o} /> : <Cost o={o} /> : null}
      </Loadable>
    </View>
  );
}

function Numbers({ win }: { win: Window }) {
  const query = useStats(win);
  const { served } = useServes('stats.get');
  const s = query.data;
  return (
    <Loadable query={query} served={served} skeleton="stats" label="stats" empty={NOTHING}>
      {s ? <Counts s={s} /> : null}
    </Loadable>
  );
}

/** What every empty part of the page says, so the three never read differently. */
const NOTHING = 'Nothing recorded for this period.';

/** The numbers and the outlook, for the Usage tab to show under the accounts. */
export function Stats() {
  const c = usePalette();
  const [win, setWin] = useState<Window>('24h');
  return (
    <View testID="stats-page" style={{ padding: space.lg, paddingBottom: space.xl * 2, gap: space.xl }}>
      <Text style={{ color: c.ink, fontSize: font.size.xl, fontWeight: '700' }}>Stats</Text>
      <Choice label="window" value={win} options={WINDOWS} onChange={setWin} />
      <Numbers win={win} />
      <OutlookSection range={win} />
    </View>
  );
}
