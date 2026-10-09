// The writes and the in-flight mark of the agent verbs (wake, interrupt, restart, close, park, fold, move account, clear stall), shared by the web menu and the phone's.
import { callOf, VERBS, type Verb, type VerbId } from '@handline/app-core';
import { usePending, useWrite } from './write.ts';

/** Starts a verb's write. */
export function useVerbRun(agent: string) {
  const wake = useWrite('session.wake', agent);
  const interrupt = useWrite('session.interrupt', agent);
  const stop = useWrite('session.stop', agent);
  const restart = useWrite('session.restart', agent);
  const park = useWrite('session.park', agent);
  const fold = useWrite('agent.fold', agent);
  const move = useWrite('agent.move', agent);
  const unstall = useWrite('agent.unstall', agent);
  return (v: Verb, account?: string) => {
    const p = callOf(v, agent, account).params as never;
    if (v.method === 'session.wake') wake.mutate(p);
    else if (v.method === 'session.interrupt') interrupt.mutate(p);
    else if (v.method === 'session.stop') stop.mutate(p);
    else if (v.method === 'session.restart') restart.mutate(p);
    else if (v.method === 'session.park') park.mutate(p);
    else if (v.method === 'agent.fold') fold.mutate(p);
    else if (v.method === 'agent.move') move.mutate(p);
    else if (v.method === 'agent.unstall') unstall.mutate(p);
  };
}

/** The gerund of the verb in flight on this agent ("parking…"), for the state cell; null when none. `skip` leaves out verbs the caller marks itself. */
export function useVerbMark(agent: string, skip: VerbId[] = []): string | null {
  const mine = <T extends { agent: string }>(l: T[]) => l.filter((v) => v.agent === agent);
  const hits: [boolean, VerbId][] = [
    [mine(usePending('session.wake')).length > 0, 'wake'],
    [mine(usePending('session.interrupt')).length > 0, 'interrupt'],
    [mine(usePending('session.stop')).some((v) => v.mode === 'close'), 'close'],
    [mine(usePending('session.restart')).length > 0, 'restart'],
    [mine(usePending('session.park')).length > 0, 'park'],
    [mine(usePending('agent.fold')).length > 0, 'fold'],
    [mine(usePending('agent.move')).length > 0, 'move'],
    [mine(usePending('agent.unstall')).length > 0, 'clear_stall'],
  ];
  const hit = hits.find(([on, id]) => on && !skip.includes(id));
  return hit ? VERBS[hit[1]].gerund : null;
}
