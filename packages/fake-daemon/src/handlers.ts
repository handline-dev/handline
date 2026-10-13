// The methods that act on a session, its input and its terminal, one function each.
import type { Actor, Caller, Notifications, Timestamp } from '@handline/contract';
import { badRequest, Failure, refused } from './errors.ts';
import type { Script } from './script.ts';
import { coded, snapshotOf } from './bytes.ts';
import { SETTINGS_HANDLERS } from './settings.ts';
import { withStructured } from './structured-controls.ts';
import type { FakeTranscript } from './transcript.ts';
import { HR_HANDLERS, teamOf } from './hr.ts';
import { DAEMON_HOLDER, row, scrolledOff, styledRow, tid, visible, type Attach, type Session } from './session.ts';

export interface Who {
  caller: Caller;
  attached: Map<string, Attach>;
}
export interface Call {
  c: Who;
  p: Record<string, unknown>;
  actor: Actor;
  script: Script;
  transcript: FakeTranscript;
  agent(): Session;
  byTerminal(id: unknown): Session;
  iso(): Timestamp;
}

/** The format an attach or history call asks for: text unless it says bytes; anything else is a bad request. */
function formatOf(p: Record<string, unknown>): 'text' | 'bytes' | 'styled' {
  if (p.format === undefined || p.format === 'text') return 'text';
  if (p.format === 'bytes' || p.format === 'styled') return p.format;
  throw badRequest(`format is text, bytes or styled, not ${String(p.format)}`, 'format');
}

const BASE: Record<string, (h: Call) => unknown> = {
  ...SETTINGS_HANDLERS,
  ...HR_HANDLERS,
  'transcript.events': ({ p, transcript }) => transcript.read(p),
  'team.get': ({ script, iso }) => teamOf([...script.sessions.values()], iso()),
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
  'agent.fold': (h) => {
    const { actor, script, agent } = h;
    const s = agent();
    script.stopSession(s, 'close', actor);
    s.sessionId = null; // the next session starts from the handoff, not from this conversation
    script.control(s, actor, 'agent.fold', `${actor.name} folded the session into its handoff`);
    return script.accepted('Folded.');
  },
  'agent.move': (h) => {
    const { p, actor, script, agent } = h;
    const s = agent();
    if (typeof p.account !== 'string' || !p.account) throw badRequest('account names an account', 'account');
    s.fx.account = p.account;
    script.control(s, actor, 'agent.move', `${actor.name} moved the agent to ${p.account}`);
    script.setState(s, s.state, actor);
    return script.accepted('Moved.');
  },
  'agent.unstall': (h) => {
    const { actor, script, agent } = h;
    const s = agent();
    if (s.state !== 'stalled') throw refused('state', `${s.fx.name} is ${s.state}, not stalled`);
    script.setState(s, 'queued', actor);
    return script.accepted(`${s.fx.name}: stalled -> queued`);
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
    const { c, p, actor, script, transcript, agent } = h;
    const s = agent();
    script.gate(s, c);
    const open = s.prompt;
    if (!open || open.id !== p.prompt)
      throw new Failure('stale_prompt', 'That prompt is no longer the open one; nothing was sent.', { current: open });
    const keys = open.options.map((o) => o.key);
    const text = (p.choice as { text?: unknown } | null)?.text;
    let given: string[];
    if (typeof p.choice === 'string') {
      if (!keys.includes(p.choice)) throw badRequest(`${p.choice} is not offered by this prompt`, 'choice');
      given = [p.choice];
    } else if (Array.isArray(p.choice)) {
      if (!open.multi) throw badRequest('this prompt takes one option key, not a list', 'choice');
      if (!p.choice.length || !p.choice.every((k) => typeof k === 'string' && keys.includes(k)))
        throw badRequest('the list holds keys this prompt offers', 'choice');
      given = p.choice as string[];
    } else if (typeof text === 'string' && text.trim()) {
      if (open.kind !== 'question') throw badRequest('typed text answers only a question', 'choice');
      given = [text]; // typed text answers a question or dialog that asks for it
    } else throw badRequest('this prompt takes one of its option keys', 'choice');
    if (transcript.owns(s.fx.name)) return (transcript.answered(s.fx.name, open.id, given[0]!, actor), script.accepted('Answered.'));
    for (const k of given) script.record(s, 'key', k, actor.name);
    script.control(s, actor, 'prompt.answer', `${actor.name} answered ${given.join(', ')}`);
    script.answer(s, given[0]!, actor);
    return script.accepted('Answered.');
  },
  'input.take': (h) => {
    const { c, actor, script, agent, iso } = h;
    const s = agent();
    if (s.holder.kind !== 'daemon' && s.holder.name !== c.caller.name) throw refused('input_holder', `${s.holder.name} holds the input`);
    if (s.holder.kind === 'daemon')
      s.holder = { kind: c.caller.kind === 'device' ? 'device' : 'person', name: c.caller.name, since: iso() };
    script.control(s, actor, 'input.take', `${actor.name} took the input`);
    script.holderChanged(s);
    return script.accepted('You hold the input.', { holder: s.holder });
  },
  'input.release': (h) => {
    const { c, actor, script, agent } = h;
    const s = agent();
    if (s.holder.name !== c.caller.name || s.holder.kind === 'daemon') throw refused('input_holder', 'you do not hold the input');
    s.holder = DAEMON_HOLDER;
    script.control(s, actor, 'input.release', `${actor.name} released the input`);
    script.holderChanged(s);
    return script.accepted('The daemon holds the input again.');
  },
  'terminal.attach': (h) => {
    const { c, p, agent, byTerminal } = h;
    const s = p.terminal !== undefined ? byTerminal(p.terminal) : agent();
    if (p.terminal !== undefined && p.agent !== undefined) throw badRequest('give agent or terminal, not both', 'terminal');
    if (!s.hasTerminal) throw refused('state', 'the agent has no terminal');
    const format = formatOf(p);
    const want = typeof p.history === 'number' ? p.history : 100;
    const off = scrolledOff(s),
      first = Math.max(0, off - want);
    if (format === 'bytes') {
      const at = { format, seq: s.seq } satisfies Attach;
      c.attached.set(tid(s), at);
      return { ...snapshotOf(s, want, [s.holder]), seq: at.seq };
    }
    c.attached.set(tid(s), { format, seq: s.seq });
    const rowOf = format === 'styled' ? (l: string) => styledRow(l, s.cols) : row;
    return {
      terminal: tid(s),
      format,
      seq: s.seq,
      cols: s.cols,
      rows: s.rows,
      lines: visible(s).map(rowOf),
      cursor: null,
      history: { first, lines: s.lines.slice(first, off).map(rowOf), more: first > 0 },
      holder: s.holder,
    };
  },
  'terminal.open': ({ p, agent }) => {
    if (typeof p.agent !== 'string') throw badRequest('agent is required', 'agent');
    const s = agent();
    if (!s.hasTerminal) throw new Failure('not_found', `${s.fx.name} has no terminal`, { field: 'terminal' });
    return { agent: s.fx.name, terminal: tid(s), host: 'fake', attach: { argv: ['fake-attach', tid(s)] }, focused: false, local: true };
  },
  'terminal.close': ({ actor, script, agent }) => {
    const s = agent();
    if (s.open) throw refused('state', `${s.fx.name} has a session running: stop it first`);
    if (!s.hasTerminal) throw refused('state', `${s.fx.name} has no terminal`);
    s.hasTerminal = false;
    script.setState(s, 'no_terminal', actor);
    return script.accepted('Terminal closed.');
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
    const lines = s.lines.slice(first, end);
    const format = formatOf(p);
    const rowOf = format === 'styled' ? (l: string) => styledRow(l, s.cols) : row;
    return { first, lines: format === 'bytes' ? lines.map((l) => coded(l, s.cols)) : lines.map(rowOf), more: first > 0 };
  },
  'terminal.resize': (h) => {
    const { c, p, script, byTerminal } = h;
    const s = byTerminal(p.terminal);
    if (s.holder.name !== c.caller.name || s.holder.kind === 'daemon') throw refused('input_holder', 'only the input holder may resize');
    s.cols = Number(p.cols);
    s.rows = Number(p.rows);
    script.pushTerminal(s, 'resize', (seq): Notifications['resize'] => ({ terminal: tid(s), seq, cols: s.cols, rows: s.rows }));
    script.snapshotBytes(s, 'resize');
    return script.accepted('Resized.');
  },
  'terminal.input': (h) => {
    const { c, p, actor, script, byTerminal } = h;
    return script.rawInput(byTerminal(p.terminal), p, c, actor);
  },
};

export const HANDLERS = withStructured(BASE);
