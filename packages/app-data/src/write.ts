// `useWrite`: the one door every UI write goes through (docs/turborepo-layout.md §8.5). An authored write edits the cache first and puts
// it back if the daemon refuses; a decided write leaves the cache alone and is shown by its pending mark.
import { useMutation, useMutationState, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useStore } from '@handline/app-react';
import type { CallParams, MethodName, Methods } from '@handline/client';
import { useData } from './provider.ts';
import { reads, type Key, type NOT_QUERIES, type ReadMethod } from './reads.ts';
import { writes, type NOT_FROM_UI, type PAIRING } from './writes.ts';
import type { WriteSpec } from './write-spec.ts';

/** Every method the UI may write: the contract's methods that are in none of the other lists. */
export type WriteMethod = Exclude<
  MethodName,
  ReadMethod | (typeof NOT_QUERIES)[number] | (typeof NOT_FROM_UI)[number] | (typeof PAIRING)[number]
>;

/** An entry as it was before the edit, and as the edit left it: a refusal restores it only if nothing else has changed it since. */
type Snapshot = { key: Key; before: unknown; after: unknown }[];
const FED = new Set<string>(Object.values(reads).flatMap((r) => ('feed' in r ? [r.resource] : [])));

const writeKey = (daemon: string, method: string) => ['w', daemon, method] as const;

/**
 * A write. `target` (an agent, a task) orders the writes on that target: they run one after another. The returned mutation has
 * `isPending`, `error` and `variables` for the pending mark and the reason.
 */
export function useWrite<M extends WriteMethod>(method: M, target?: string) {
  const store = useStore();
  const qc = useQueryClient();
  const { daemon } = useData();
  const spec = writes[method] as WriteSpec;
  const ctx = (): Parameters<NonNullable<WriteSpec['patch']>>[0] => ({ qc, d: daemon, me: store.me, now: new Date().toISOString() });
  return useMutation<Methods[M]['result'], unknown, CallParams<M>, { snapshot: Snapshot }>({
    mutationKey: writeKey(daemon, method),
    scope: target ? { id: `${daemon}:${target}` } : undefined,
    mutationFn: (p) => store.client.call(method, p),
    onMutate: async (p) => {
      if (spec.kind !== 'authored') return { snapshot: [] };
      const keys = spec.touches?.(daemon, p as never) ?? [];
      await Promise.all(keys.map((queryKey) => qc.cancelQueries({ queryKey })));
      const before = keys.flatMap((queryKey) => qc.getQueriesData({ queryKey }));
      spec.patch?.(ctx(), p as never);
      return { snapshot: before.map(([key, data]) => ({ key, before: data, after: qc.getQueryData(key) })) };
    },
    onError: (e, p, mine) => {
      const handled = spec.fail?.(ctx(), p as never, e) ?? false;
      if (!handled) restore(qc, mine?.snapshot ?? []);
      store.reportError(spec.label, e);
    },
    onSuccess: (r, p) => {
      spec.reconcile?.(ctx(), p as never, r as never);
      store.dismissError();
    },
    onSettled: (_r, _e, p) => settle(qc, daemon, spec, p as never),
  });
}

/**
 * Back to the entries as they were before the edit. An entry an event (or another write) changed meanwhile holds the daemon's newer
 * truth, so it is read again instead of being overwritten with the old value.
 */
function restore(qc: QueryClient, snapshot: Snapshot) {
  for (const { key, before, after } of snapshot) {
    if (qc.getQueryData(key) === after) qc.setQueryData(key, before);
    else void qc.invalidateQueries({ queryKey: key });
  }
}

/** Only entries no topic keeps current are read again; the others get the daemon's event. */
function settle(qc: QueryClient, daemon: string, spec: WriteSpec, p: never) {
  for (const queryKey of spec.touches?.(daemon, p) ?? []) if (!FED.has(String(queryKey[2]))) void qc.invalidateQueries({ queryKey });
}

/** The variables of this method's writes now in flight, for a pending mark anywhere in the tree ("starting…", a card's buttons off). */
export function usePending<M extends WriteMethod>(method: M): CallParams<M>[] {
  const { daemon } = useData();
  return useMutationState({
    filters: { mutationKey: writeKey(daemon, method), status: 'pending' },
    select: (m) => m.state.variables as CallParams<M>,
  });
}
