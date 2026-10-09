// One region that shows daemon data in its five states (docs/turborepo-layout.md §8.3): the skeleton on first load, the data with a
// quiet marker while it refreshes, the empty sentence, an error block that keeps the last data beneath it, and "not served".
import type { ReactNode } from 'react';
import { Text, View } from 'react-native';
import { classify, sentence, viewState, type SkeletonName } from '@handline/app-data';
import { space } from '@handline/tokens';
import { Skeleton } from '../skeleton.tsx';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';

interface Query {
  isPending: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  data: unknown;
  refetch: () => unknown;
}

export function Loadable(props: {
  query: Query;
  served: boolean;
  isEmpty?: boolean;
  skeleton: SkeletonName;
  /** What the region shows, for "Loading …" and "This daemon does not serve …". */
  label: string;
  empty: string;
  /** A refused read is an error to retry, not "not served" (a view with no other way to show the thing). */
  retryRefused?: boolean;
  children: ReactNode;
}) {
  const c = usePalette();
  const q = props.query;
  const state = viewState(q, props.served, props.isEmpty);
  const failure = q.isError ? classify(q.error) : null;
  const note = (testID: string, text: string, color = c.muted) => (
    <Text testID={testID} style={{ color, padding: space.lg }}>
      {text}
    </Text>
  );
  if (state === 'not_served') return note('not-served', `This daemon does not serve ${props.label}.`);
  if (state === 'loading') return <Skeleton shape={props.skeleton} loading label={props.label} />;
  if (state === 'error' && failure) {
    // A lost connection says nothing here (the banner does) and a missing thing is just empty.
    if (failure.kind === 'transport') return q.data === undefined ? null : <>{props.children}</>;
    if (failure.kind === 'not_found') return note('empty', props.empty);
    if (failure.kind === 'not_served' && !props.retryRefused) return note('not-served', `This daemon does not serve ${props.label}.`);
    return (
      <View style={{ flex: 1 }}>
        <View testID="error" accessibilityRole="alert" style={{ padding: space.lg, gap: space.sm }}>
          <Text style={{ color: c.block }}>{sentence(failure)}</Text>
          <Button label="Retry" onPress={() => void q.refetch()} />
        </View>
        {q.data !== undefined && props.children}
      </View>
    );
  }
  if (state === 'empty') return note('empty', props.empty);
  return (
    <View style={{ flex: 1 }}>
      {state === 'refreshing' && (
        <Text testID="refreshing" style={{ color: c.muted, fontSize: 11, paddingHorizontal: space.lg }}>
          Updating…
        </Text>
      )}
      {props.children}
    </View>
  );
}
