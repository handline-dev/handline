// The graph of the open tasks: a box per task, an arrow from the task that holds to the task that waits (the layout is app-core's
// `taskGraph`). The scroll views are the pan, the buttons the zoom; a tap on a box opens the task.
import { useMemo, useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import Svg, { G, Path, Rect, Text as SvgText } from 'react-native-svg';
import { taskGraph, taskRef, type GraphEdge } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { Task } from '@handline/client';
import { toneOf, type Tone } from '@handline/app-ui';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';

const d = (e: GraphEdge) => e.points.map((p, i) => `${i ? 'L' : 'M'}${p.x} ${p.y}`).join(' ');
const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

export function TaskGraph(props: { tasks: readonly Task[]; onSelect: (id: number) => void }) {
  const c = usePalette();
  const me = useStore().me;
  const g = useMemo(() => taskGraph(props.tasks, { me }), [props.tasks, me]);
  const [k, setK] = useState(1);
  const tone: Record<Tone, string> = { run: c.run, wait: c.wait, block: c.block, idle: c.idle };
  if (g.nodes.length === 0) return <Text style={{ color: c.muted, padding: space.lg }}>No open tasks.</Text>;
  return (
    <View style={{ flex: 1 }}>
      <View style={{ flexDirection: 'row', gap: space.sm, paddingHorizontal: space.lg, paddingBottom: space.sm }}>
        <Button testID="graph-zoom-out" label="−" onPress={() => setK((v) => Math.max(0.4, v / 1.25))} />
        <Button testID="graph-zoom-in" label="+" onPress={() => setK((v) => Math.min(3, v * 1.25))} />
      </View>
      <ScrollView horizontal>
        <ScrollView testID="task-graph">
          <Svg width={g.width * k} height={g.height * k} viewBox={`0 0 ${g.width} ${g.height}`}>
            {g.edges.map((e) => (
              <Path key={`${e.from}-${e.to}`} d={d(e)} stroke={c.muted} strokeWidth={1.5} fill="none" />
            ))}
            {g.nodes.map((n) => (
              <G key={n.id} onPress={() => props.onSelect(n.id)} testID={`graph-node-${n.id}`}>
                <Rect
                  x={n.x}
                  y={n.y}
                  width={n.w}
                  height={n.h}
                  rx={6}
                  fill={c.panel}
                  stroke={tone[toneOf(n.state)]}
                  strokeWidth={n.mine ? 2.5 : 1}
                />
                <SvgText x={n.x + 8} y={n.y + 18} fontSize={12} fill={c.ink}>
                  {`${taskRef(n.id)} · ${n.state}`}
                </SvgText>
                <SvgText x={n.x + 8} y={n.y + 34} fontSize={11} fill={c.muted}>
                  {clip(n.reason ?? n.title, 26)}
                </SvgText>
              </G>
            ))}
          </Svg>
        </ScrollView>
      </ScrollView>
    </View>
  );
}
