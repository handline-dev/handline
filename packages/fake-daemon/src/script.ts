// What the scripted sessions do: the screen text, the turn script, the prompt and the session lifecycle. The daemon owns
// the connections and the event log; this class reaches them through `Host`.
import type { Actor, Caller, Notifications, Prompt, Timestamp, Topic } from '@handline/contract';
import { FakeHr } from './hr.ts';
import { BANNER, PROMPT_LINES } from './fixtures.ts';
import { badRequest, refused } from './errors.ts';
import { outputFor, snapshotOf, typedParts } from './bytes.ts';
import { row, tid, view, visible, type Session } from './session.ts';

export interface Host {
  iso(): Timestamp;
  sessions(): Map<string, Session>;
  emit(topic: Topic, type: string, f?: { agent?: string; actor?: Actor; title?: string; data?: Record<string, unknown> }): void;
  /** Sends a terminal notice to every connection attached to the session's terminal. */
  broadcast(s: Session, method: string, params: unknown): void;
  /** Sends a notice to the connections watching in bytes, each with its own seq. */
  broadcastBytes(s: Session, method: 'output' | 'snapshot', build: () => object): void;
}
type Who = { caller: Caller };
const KEYS = new Set(['esc', 'enter', 'up', 'down', 'tab', 'shift-tab', 'ctrl-c', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']);

export class Script {
  private timelineId = 0;
  readonly hr: FakeHr;
  private host: Host;
  get sessions() {
    return this.host.sessions();
  }
  constructor(host: Host) {
    this.host = host;
    this.hr = new FakeHr(host);
  }

  record(s: Session, kind: 'text' | 'key', value: string, actor: string) {
    s.received.push({ kind, value, actor });
  }

  /** Applies a change to the screen text and sends the changed rows to everyone attached. */
  edit(s: Session, change: () => void): void {
    const before = visible(s);
    change();
    const changed = visible(s).flatMap((text, y) => (text !== before[y] ? [{ y, row: row(text) }] : []));
    if (!changed.length) return;
    this.pushTerminal(s, 'frame', (seq): Notifications['frame'] => ({ terminal: tid(s), seq, full: false, changed, cursor: null }));
    this.host.broadcastBytes(s, 'output', () => ({
      terminal: tid(s),
      data: outputFor(
        s,
        changed.map((c) => c.y),
      ),
    }));
  }
  /** Tells bytes watchers to repaint: the size changed, or (`slow_client`) they fell behind. */
  snapshotBytes(s: Session, reason: 'resize' | 'slow_client'): void {
    this.host.broadcastBytes(s, 'snapshot', () => {
      const { terminal, cols, rows, data, alt_screen, cursor, cursor_visible } = snapshotOf(s, 0, [s.holder]);
      return { terminal, reason, cols, rows, data, alt_screen, cursor, cursor_visible };
    });
  }
  pushTerminal(s: Session, method: 'frame' | 'resize' | 'marker', build: (seq: number) => object): void {
    const params = build(++s.seq);
    this.host.broadcast(s, method, params);
  }

  setState(s: Session, state: string, actor?: Actor) {
    s.state = state;
    s.since = this.host.iso();
    this.host.emit('agents', 'agent.changed', { agent: s.fx.name, actor, data: view(s) as unknown as Record<string, unknown> });
  }
  control(s: Session, actor: Actor, kind: string, text: string) {
    this.host.emit('agents', 'agent.control', {
      agent: s.fx.name,
      actor,
      data: { id: ++this.timelineId, at: this.host.iso(), kind, actor, text },
    });
  }

  write(s: Session, line: string) {
    this.edit(s, () => {
      s.lines.push(line);
    });
  }

  setComposeLine(s: Session) {
    this.edit(s, () => {
      const line = `> ${s.buffer}`;
      if (s.composing) s.lines[s.lines.length - 1] = line;
      else s.lines.push(line);
      s.composing = true;
    });
  }

  submit(s: Session, actor: Actor) {
    const text = s.buffer;
    this.edit(s, () => {
      const line = `> ${text}`;
      if (s.composing) s.lines[s.lines.length - 1] = line;
      else s.lines.push(line);
      s.composing = false;
      s.buffer = '';
    });
    if (s.state === 'idle') this.startTurn(s, actor);
  }

  startTurn(s: Session, actor: Actor) {
    this.setState(s, 'running', actor);
    s.queue = [() => this.write(s, '● Thinking…'), () => this.write(s, '● I will push the branch.'), () => this.openPrompt(s)];
  }

  openPrompt(s: Session) {
    const lines = PROMPT_LINES.map((l) => l.replace('{branch}', s.fx.branch));
    const prompt: Prompt = {
      id: `p${++s.counter}-${s.fx.name}`,
      agent: s.fx.name,
      kind: 'permission',
      tool: 'Bash',
      command: `git push origin ${s.fx.branch}`,
      reason: null,
      lines,
      since: this.host.iso(),
      options: [
        { key: '1', label: 'Yes', answer: 'yes', highlighted: true },
        { key: '2', label: 'No', answer: 'no' },
      ],
    };
    s.prompt = prompt;
    for (const l of lines) this.write(s, l);
    this.setState(s, 'waiting');
    this.host.emit('notifications', 'prompt_asked', {
      agent: s.fx.name,
      title: 'Permission: Bash',
      data: prompt as unknown as Record<string, unknown>,
    });
  }

  answer(s: Session, key: string, actor: Actor) {
    s.prompt = null;
    if (key === '1') {
      s.queue = [
        () => {
          this.setState(s, 'running');
          this.write(s, `● Ran git push origin ${s.fx.branch}`);
        },
        () => this.write(s, '● Pushed. Done.'),
        () => this.setState(s, 'idle'),
      ];
    } else {
      s.queue = [
        () => {
          this.write(s, '● Understood, I will not push.');
          this.setState(s, 'idle');
        },
      ];
    }
    this.setState(s, 'running', actor);
  }

  interruptTurn(s: Session, actor: Actor) {
    if (s.state === 'idle') return;
    s.prompt = null;
    s.queue = [];
    this.write(s, '● Interrupted · What should Claude do instead?');
    this.setState(s, 'idle', actor);
  }

  pressKey(s: Session, key: string, actor: Actor) {
    this.record(s, 'key', key, actor.name);
    if (key === 'esc' || key === 'ctrl-c') this.interruptTurn(s, actor);
    else if (key === 'enter') {
      if (s.buffer) this.submit(s, actor);
    } else if (s.prompt?.options.some((o) => o.key === key)) this.answer(s, key, actor);
  }

  accepted(said: string, extra: Record<string, unknown> = {}) {
    return { accepted: true, said, ...extra };
  }

  /** The gates every typing method passes: a session to type at, and nobody else holding the input. */
  gate(s: Session, c: Who) {
    if (!s.open) throw refused('state', 'the agent has no open session');
    if (s.holder.kind !== 'daemon' && s.holder.name !== c.caller.name) throw refused('input_holder', `${s.holder.name} holds the input`);
  }

  /** Opens a session; the new screen follows the old text, as a terminal's scrollback would. */
  startSession(s: Session, wake: boolean, actor: Actor) {
    if (s.open) throw refused('state', 'the session is already open');
    if (wake && !s.sessionId) throw refused('state', 'there is no closed session to resume');
    s.open = true;
    s.hasTerminal = true;
    s.sessionId ??= `s-${s.fx.name}-${++s.counter}`;
    this.edit(s, () => {
      s.lines.push(...BANNER.map((l) => l.replace('{worktree}', s.fx.worktree)));
    });
    this.setState(s, 'idle', actor);
    return this.accepted('Session started.');
  }

  restartSession(s: Session, fresh: boolean, actor: Actor) {
    if (s.open) this.stopSession(s, 'close', actor);
    if (fresh) s.sessionId = null;
    return this.startSession(s, false, actor);
  }

  stopSession(s: Session, mode: unknown, actor: Actor) {
    if (mode !== 'exit' && mode !== 'close' && mode !== 'kill') throw badRequest('mode must be exit, close or kill', 'mode');
    if (!s.open) throw refused('state', 'the agent has no open session');
    s.prompt = null;
    s.queue = [];
    s.open = false;
    if (mode === 'kill') s.hasTerminal = false;
    else this.write(s, '(session ended)');
    this.setState(s, mode === 'kill' ? 'no_terminal' : 'parked', actor);
    return this.accepted('Session ended.');
  }

  input(s: Session, p: Record<string, unknown>, c: Who, actor: Actor) {
    if (typeof p.text !== 'string') throw badRequest('input needs text', 'text');
    this.gate(s, c);
    if (s.fx.wall) throw refused('wall', 'the account is at its usage limit; typing would only spend quota');
    if (s.prompt) throw refused('screen_blocker', 'a dialog is open, so a typed line would answer it', 'use keys or prompt.answer');
    this.record(s, 'text', p.text, actor.name);
    this.control(s, actor, 'input', `${actor.name} typed`);
    s.buffer += p.text;
    if (p.enter !== false) this.submit(s, actor);
    else this.setComposeLine(s);
    return this.accepted('Typed.');
  }

  /** Raw bytes from a terminal client: text is typed, CR is Enter, 0x03 and a lone ESC are the interrupt keys. */
  rawInput(s: Session, p: Record<string, unknown>, c: Who, actor: Actor) {
    const bytes = typeof p.data === 'string' ? Buffer.from(p.data, 'base64') : Buffer.alloc(0);
    if (!bytes.length || bytes.length > 16384) throw badRequest('data is 1 to 16384 bytes of base64', 'data');
    if (s.holder.kind === 'daemon') throw refused('input_holder', 'take the input first: the daemon holds it');
    this.gate(s, c);
    this.control(s, actor, 'input', `${actor.name} typed`);
    for (const part of typedParts(bytes.toString('utf8'))) {
      if ('key' in part) this.pressKey(s, part.key, actor);
      else {
        this.record(s, 'text', part.text, actor.name);
        s.buffer += part.text;
        this.setComposeLine(s);
      }
    }
    return this.accepted('Typed.');
  }

  keys(s: Session, keys: unknown, c: Who, actor: Actor) {
    if (!Array.isArray(keys) || !keys.length || !keys.every((k) => KEYS.has(k))) throw badRequest('keys must be named keys', 'keys');
    this.gate(s, c);
    this.control(s, actor, 'keys', `${actor.name} pressed ${keys.join(' ')}`);
    for (const k of keys as string[]) this.pressKey(s, k, actor);
    return this.accepted('Keys sent.');
  }
}
