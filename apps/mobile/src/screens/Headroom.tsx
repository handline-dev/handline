// The Headroom view of the Stats page (stats.headroom): the share of the usage windows left, three days back as a solid line and as many ahead
// as a dashed guess, now in the middle. Weekly resets are tall ticks on the floor, 5 h resets short ones. A finger dragged over the graph puts a
// line and a balloon with the percent at that time, and they stay after the finger lifts. Under it, each account's share and its next reset.
import { useMemo, useRef, useState } from 'react';
import { PanResponder, Text, View } from 'react-native';
import Svg, { Line, Path } from 'react-native-svg';
import { dateTime, headroomPoints, headroomStamp, headroomSummary, nearestPoint, span } from '@handline/app-core';
import type { Headroom } from '@handline/client';
import { useHeadroom, useServes } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';

const DAY_MS = 86_400_000;
const H = 180;
const AXIS = 48;
const TICK_GAP = 64;

/** Seconds between points so a span gets about 240: a minute at least, a whole number of minutes. */
const stepFor = (days: number) => Math.max(60, Math.ceil((days * 2 * 86_400) / 240 / 60) * 60);

type Pt = { at: string; pct: number | null };
const effective = (p: { at: string; five_left: number | null; week_left: number | null }): Pt => ({
  at: p.at,
  pct: p.five_left === null || p.week_left === null ? (p.five_left ?? p.week_left) : Math.min(p.five_left, p.week_left),
});

/** The weekly share averaged over the accounts: the floor under the 5 h swings. */
function weekFloor(h: Headroom, side: 'past' | 'future'): Pt[] {
  return (h.accounts[0]?.[side] ?? []).map((p, i) => {
    const v = h.accounts.map((a) => a[side][i]?.week_left).filter((n): n is number => typeof n === 'number');
    return { at: p.at, pct: v.length ? v.reduce((a, b) => a + b, 0) / v.length : null };
  });
}

function Chart({ h }: { h: Headroom }) {
  const c = usePalette();
  const [width, setWidth] = useState(0);
  const [cursor, setCursor] = useState<number | null>(null);
  const all = headroomPoints(h);
  const t0 = Date.parse(all[0]?.at ?? h.now);
  const t1 = Date.parse(all.at(-1)?.at ?? h.now);
  const now = Date.parse(h.now);
  const x = (t: number) => (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * width);
  const y = (pct: number) => H - (Math.min(100, Math.max(0, pct)) / 100) * H;
  const path = (pts: Pt[]) =>
    pts
      .filter((p) => p.pct !== null)
      .map((p, i) => `${i ? 'L' : 'M'}${x(Date.parse(p.at)).toFixed(1)},${y(p.pct!).toFixed(1)}`)
      .join(' ');
  const line = useMemo(
    () => ({
      past: h.combined.past.map((p) => ({ at: p.at, pct: p.left_pct })),
      future: h.combined.future.map((p) => ({ at: p.at, pct: p.left_pct })),
    }),
    [h],
  );
  const floor = useMemo(() => ({ past: weekFloor(h, 'past'), future: weekFloor(h, 'future') }), [h]);
  const resets = h.accounts.flatMap((a) => a.resets.map((r) => ({ ...r, account: a.account })));
  // The responder reads the latest geometry through a ref: the pan handlers are made once.
  const geo = useRef({ width, t0, t1 });
  geo.current = { width, t0, t1 };
  const pan = useMemo(() => {
    const at = (px: number) => {
      const g = geo.current;
      if (g.width > 0) setCursor(g.t0 + Math.min(1, Math.max(0, px / g.width)) * (g.t1 - g.t0));
    };
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: (e) => at(e.nativeEvent.locationX),
      onPanResponderMove: (e) => at(e.nativeEvent.locationX),
    });
  }, []);
  const read = cursor === null ? null : nearestPoint([...line.past, ...line.future], cursor, (p) => p.pct !== null);
  const floorRead = cursor === null ? null : nearestPoint([...floor.past, ...floor.future], cursor, (p) => p.pct !== null);
  // The densest tick spacing whose labels still fit side by side: hours of the day up to 12 h, then days.
  const ticks = useMemo(() => {
    const out: { t: number; label: string }[] = [];
    const every = [6, 12, 24, 48, 96].find((h) => (width * h * 3_600_000) / Math.max(1, t1 - t0) >= TICK_GAP) ?? 96;
    const d = new Date(t0);
    d.setHours(every < 24 ? Math.ceil(d.getHours() / every) * every : d.getHours() > 0 ? 24 : 0, 0, 0, 0);
    // A label that would run past either edge is left out rather than shifted against its neighbour.
    for (; d.getTime() <= t1; d.setHours(d.getHours() + every))
      if (x(d.getTime()) >= 36 && x(d.getTime()) <= width - 36)
        out.push({ t: d.getTime(), label: d.toLocaleString([], every < 24 ? { hour: 'numeric' } : { weekday: 'short', day: 'numeric' }) });
    return out;
  }, [t0, t1, width]);
  const balloonX = cursor === null ? 0 : Math.min(Math.max(x(cursor), 115), Math.max(115, width - 115));
  return (
    <View testID="headroom-plot" style={{ gap: space.xs }}>
      <View style={{ flexDirection: 'row' }}>
        <View style={{ width: AXIS, height: H }} accessibilityElementsHidden>
          {[100, 50, 0].map((g) => (
            <Text
              key={g}
              style={{
                position: 'absolute',
                right: 4,
                top: Math.min(H - 14, Math.max(0, y(g) - 7)),
                color: c.hint,
                fontSize: font.size.label,
              }}
            >
              {g}%
            </Text>
          ))}
        </View>
        <View
          testID="headroom-graph"
          accessibilityRole="image"
          accessibilityLabel={`Share of the usage windows left across ${h.accounts.length} accounts, recorded and guessed. Drag a finger over it to read a time.`}
          onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
          style={{ flex: 1, height: H }}
          {...pan.panHandlers}
        >
          {width > 0 ? (
            <Svg width={width} height={H} pointerEvents="none">
              {[0, 50, 100].map((g) => (
                <Line key={g} x1={0} x2={width} y1={y(g)} y2={y(g)} stroke={c.line} strokeWidth={1} />
              ))}
              {resets.map((r) => (
                <Line
                  key={`${r.at}${r.window}${r.account}`}
                  x1={x(Date.parse(r.at))}
                  x2={x(Date.parse(r.at))}
                  y1={H}
                  y2={r.window === '7d' ? H - 18 : H - 7}
                  stroke={r.window === '7d' ? c.ink : c.lineStrong}
                  strokeWidth={r.window === '7d' ? 2 : 1}
                />
              ))}
              <Path d={path(line.past)} stroke={c.hint} strokeWidth={1.5} fill="none" />
              <Path d={path(line.future)} stroke={c.hint} strokeWidth={1.5} fill="none" strokeDasharray="5 4" />
              <Path d={path(floor.past)} stroke={c.ink} strokeWidth={3} fill="none" />
              <Path d={path(floor.future)} stroke={c.ink} strokeWidth={3} fill="none" strokeDasharray="6 4" />
              <Line x1={x(now)} x2={x(now)} y1={0} y2={H} stroke={c.accent} strokeWidth={1.5} />
              {cursor === null ? null : <Line x1={x(cursor)} x2={x(cursor)} y1={0} y2={H} stroke={c.ink} strokeWidth={1} />}
            </Svg>
          ) : null}
          <Text
            style={{
              position: 'absolute',
              top: 0,
              left: Math.max(0, x(now) - 36),
              width: 72,
              textAlign: 'center',
              color: c.accent,
              fontSize: font.size.label,
            }}
            pointerEvents="none"
          >
            now
          </Text>
          {cursor !== null && read ? (
            <View
              testID="headroom-balloon"
              pointerEvents="none"
              style={{
                position: 'absolute',
                top: 18,
                left: balloonX - 115,
                width: 230,
                alignItems: 'center',
                paddingVertical: space.xs,
                paddingHorizontal: space.sm,
                borderRadius: radius.card,
                backgroundColor: c.raised,
                borderWidth: 1,
                borderColor: c.line,
              }}
            >
              <Text style={{ color: c.ink, fontSize: font.size.label }}>{headroomStamp(read.at, read.pct)}</Text>
              {floorRead ? (
                <Text style={{ color: c.muted, fontSize: font.size.label }}>week floor {Math.round(floorRead.pct!)}%</Text>
              ) : null}
            </View>
          ) : null}
        </View>
      </View>
      <View style={{ marginLeft: AXIS, height: 20 }} accessibilityElementsHidden>
        {ticks.map((k) => (
          <Text
            key={k.t}
            numberOfLines={1}
            style={{
              position: 'absolute',
              left: Math.max(0, x(k.t) - 36),
              width: 72,
              textAlign: 'center',
              color: c.hint,
              fontSize: font.size.label,
            }}
          >
            {k.label}
          </Text>
        ))}
      </View>
      <Text style={{ color: c.muted, fontSize: font.size.label }}>
        Solid: recorded. Dashed: guess. Bold: week left; faint: 5 h swings. Tall tick: weekly reset; short tick: 5 h reset.
      </Text>
    </View>
  );
}

/** Each account's share now and when its windows reset next. */
function Accounts({ h }: { h: Headroom }) {
  const c = usePalette();
  const now = Date.parse(h.now);
  return (
    <View testID="headroom-accounts" style={{ gap: space.sm }}>
      {h.accounts.map((a) => {
        const last = [...a.past]
          .reverse()
          .map(effective)
          .find((p) => p.pct !== null);
        const next = (w: '5h' | '7d') => a.resets.find((r) => r.window === w && Date.parse(r.at) > now);
        const resets = [
          next('5h') && `5 h in ${span(Date.parse(next('5h')!.at) - now)}`,
          next('7d') && `week ${dateTime(next('7d')!.at)}`,
        ].filter(Boolean);
        return (
          <View key={a.account} testID={`headroom-${a.account}`}>
            <Text numberOfLines={1} style={{ color: c.ink }}>
              {a.account}
            </Text>
            <Text style={{ color: c.muted }}>
              {last ? `${Math.round(last.pct!)}% left` : 'no reading'}
              {resets.length ? ` · resets ${resets.join(', ')}` : ''}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

/** The headroom for the days picked; the span is fixed per choice, since a clock read on every render would make a new query each time. */
export function HeadroomView({ days }: { days: 1 | 3 | 7 }) {
  const c = usePalette();
  const range = useMemo(() => {
    const now = Math.floor(Date.now() / 60_000) * 60_000;
    return { from: new Date(now - days * DAY_MS).toISOString(), to: new Date(now + days * DAY_MS).toISOString() };
  }, [days]);
  const query = useHeadroom(range.from, range.to, stepFor(days));
  const { served } = useServes('stats.headroom');
  const h = query.data;
  return (
    <Loadable
      query={query}
      served={served}
      skeleton="stats"
      label="the headroom"
      empty="Nothing recorded for this period."
      isEmpty={!!h && !h.accounts.length}
    >
      {h ? (
        <View style={{ gap: space.md }}>
          <Text testID="headroom-summary" style={{ color: c.ink, fontWeight: '600' }}>
            {headroomSummary(h)}
          </Text>
          <Chart h={h} />
          <Accounts h={h} />
        </View>
      ) : null}
    </Loadable>
  );
}
