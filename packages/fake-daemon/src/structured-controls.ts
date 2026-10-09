// What differs for an agent played as a structured session (x-structured-controls): it has no terminal and no input holder, the typing
// and terminal methods are refused for it (unless a test asked for the old shape with a terminal), and interrupt and close look at whether a turn runs.
import { Failure, refused } from './errors.ts';
import type { Call } from './handlers.ts';

type Handler = (h: Call) => unknown;

/** Agent-addressed methods that need a terminal to type at. */
const NO_TERMINAL = new Set(['session.input', 'session.keys', 'session.type', 'input.take', 'input.release']);
const turnRuns = (state: string) => state === 'running' || state === 'waiting';

function structuredHandler(method: string, fn: Handler): Handler {
  if (NO_TERMINAL.has(method))
    return (h) => {
      const s = typeof h.p.agent === 'string' ? h.agent() : null;
      if (s?.structured && !s.hasTerminal)
        throw refused('unsupported', `${s.fx.name} runs as a structured session and has no terminal`, 'send a message with chat.send');
      return fn(h);
    };
  if (method === 'terminal.attach')
    return (h) => {
      const s = typeof h.p.agent === 'string' ? h.agent() : null;
      if (s?.structured && !s.hasTerminal) throw new Failure('not_found', `${s.fx.name} has no terminal`);
      return fn(h);
    };
  if (method === 'session.interrupt')
    return (h) => {
      const s = h.agent();
      if (!s.structured) return fn(h);
      if (!turnRuns(s.state)) throw refused('state', `${s.fx.name} has no turn running`);
      h.script.control(s, h.actor, 'interrupt', `${h.actor.name} interrupted`);
      h.script.interruptTurn(s, h.actor);
      return h.script.accepted('Interrupted.');
    };
  if (method === 'session.stop')
    return (h) => {
      const s = h.agent();
      if (s.structured && h.p.mode !== 'kill' && s.open && turnRuns(s.state))
        throw refused('state', `${s.fx.name} is mid-turn: interrupt it first`);
      return fn(h);
    };
  return fn;
}

/** The handlers with the structured agent's differences wrapped around the ones that have any. */
export const withStructured = (handlers: Record<string, Handler>): Record<string, Handler> =>
  Object.fromEntries(Object.entries(handlers).map(([m, fn]) => [m, structuredHandler(m, fn)]));
