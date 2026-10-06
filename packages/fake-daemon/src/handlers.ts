// The methods that act on a session, its input and its terminal, one function each.
import type { Actor, Caller, Notifications, Timestamp } from '@handline/contract';
import { badRequest, Failure, refused } from './errors.ts';
import type { Script } from './script.ts';
import { DAEMON_HOLDER, row, scrolledOff, tid, visible, type Session } from './session.ts';

export interface Who {
  caller: Caller;
  attached: Set<string>;
}
export interface Call {
  c: Who;
  p: Record<string, unknown>;
  actor: Actor;
  script: Script;
  agent(): Session;
  byTerminal(id: unknown): Session;
  iso(): Timestamp;
}

export const HANDLERS: Record<string, (h: Call) => unknown> = {
  'session.start': (h) => {
    const { actor, script, agent } = h;
    return script.startSession(agent(), false, actor);
  },
  'session.wake': (h) => {
    const { actor, script, agent } = h;
    return script.startSession(agent(), true, actor);
  },
  'session.restart': (h) => {
    const { p, actor, script, agent } = h;
    return script.restartSession(agent(), p.fresh === true, actor);
  },
  'session.park': (h) => {
    const { actor, script, agent } = h;
    return script.stopSession(agent(), 'close', actor);
  },
  'session.stop': (h) => {
    const { p, actor, script, agent } = h;
    return script.stopSession(agent(), p.mode, actor);
  },
  'session.input': (h) => {
    const { c, p, actor, script, agent } = h;
    return script.input(agent(), p, c, actor);
  },
  'session.keys': (h) => {
    const { c, p, actor, script, agent } = h;
    return script.keys(agent(), p.keys, c, actor);
  },
  'session.interrupt': (h) => {
    const { c, actor, script, agent } = h;
    const s = agent();
    script.gate(s, c);
    script.record(s, 'key', 'esc', actor.name);
    script.control(s, actor, 'interrupt', `${actor.name} interrupted`);
    script.interruptTurn(s, actor);
    return script.accepted('Interrupted.');
  },
  'prompt.answer': (h) => {
    const { c, p, actor, script, agent } = h;
    const s = agent();
    script.gate(s, c);
    const open = s.prompt;
    if (!open || open.id !== p.prompt)
      throw new Failure('stale_prompt', 'That prompt is no longer the open one; nothing was sent.', { current: open });
    if (typeof p.choice !== 'string') throw badRequest('this prompt takes one of its option keys', 'choice');
    const key = p.choice;
    if (!open.options.some((o) => o.key === key)) throw badRequest(`${key} is not offered by this prompt`, 'choice');
    script.record(s, 'key', key, actor.name);
    script.control(s, actor, 'prompt.answer', `${actor.name} answered ${key}`);
    script.answer(s, key, actor);
    return script.accepted('Answered.');
  },
  'input.take': (h) => {
    const { c, actor, script, agent, iso } = h;
    const s = agent();
    if (s.holder.kind !== 'daemon' && s.holder.name !== c.caller.name) throw refused('input_holder', `${s.holder.name} holds the input`);
    if (s.holder.kind === 'daemon')
      s.holder = { kind: c.caller.kind === 'device' ? 'device' : 'person', name: c.caller.name, since: iso() };
    script.control(s, actor, 'input.take', `${actor.name} took the input`);
    return script.accepted('You hold the input.', { holder: s.holder });
  },
  'input.release': (h) => {
    const { c, actor, script, agent } = h;
    const s = agent();
    if (s.holder.name !== c.caller.name || s.holder.kind === 'daemon') throw refused('input_holder', 'you do not hold the input');
    s.holder = DAEMON_HOLDER;
    script.control(s, actor, 'input.release', `${actor.name} released the input`);
    return script.accepted('The daemon holds the input again.');
  },
  'terminal.attach': (h) => {
    const { c, p, agent, byTerminal } = h;
    const s = p.terminal !== undefined ? byTerminal(p.terminal) : agent();
    if (p.terminal !== undefined && p.agent !== undefined) throw badRequest('give agent or terminal, not both', 'terminal');
    if (!s.hasTerminal) throw refused('state', 'the agent has no terminal');
    c.attached.add(tid(s));
    const want = typeof p.history === 'number' ? p.history : 100;
    const off = scrolledOff(s),
      first = Math.max(0, off - want);
    return {
      terminal: tid(s),
      format: 'text',
      seq: s.seq,
      cols: s.cols,
      rows: s.rows,
      lines: visible(s).map(row),
      cursor: null,
      history: { first, lines: s.lines.slice(first, off).map(row), more: first > 0 },
      holder: s.holder,
    };
  },
  'terminal.detach': (h) => {
    const { c, p } = h;
    c.attached.delete(String(p.terminal));
    return {};
  },
  'terminal.history': (h) => {
    const { p, byTerminal } = h;
    const s = byTerminal(p.terminal);
    const end = Math.min(Number(p.before), scrolledOff(s)),
      first = Math.max(0, end - Number(p.limit));
    return { first, lines: s.lines.slice(first, end).map(row), more: first > 0 };
  },
  'terminal.resize': (h) => {
    const { c, p, script, byTerminal } = h;
    const s = byTerminal(p.terminal);
    if (s.holder.name !== c.caller.name || s.holder.kind === 'daemon') throw refused('input_holder', 'only the input holder may resize');
    s.cols = Number(p.cols);
    s.rows = Number(p.rows);
    script.pushTerminal(s, 'resize', (seq): Notifications['resize'] => ({ terminal: tid(s), seq, cols: s.cols, rows: s.rows }));
    return script.accepted('Resized.');
  },
};
