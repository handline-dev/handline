// What the scripted sessions do with typing: text, raw bytes and keys; the rest is in ScriptCore.
import type { Actor, Caller } from '@handline/contract';
import { badRequest, refused } from '#src/errors.ts';
import { typedParts } from '#src/bytes.ts';
import { ScriptCore, type Host } from '#src/scriptcore.ts';
import type { Session } from '#src/session.ts';

export type { Host };
type Who = { caller: Caller };
const KEYS = new Set(['esc', 'enter', 'up', 'down', 'tab', 'shift-tab', 'ctrl-c', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']);

export class Script extends ScriptCore {
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
    if (s.handle) return this.shellInput(s, bytes.toString('utf8'));
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

  /** A shell has no holder and no agent: what is typed shows after its prompt, Enter starts a new prompt line. */
  private shellInput(s: Session, text: string) {
    this.edit(s, () => {
      for (const ch of text) {
        if (ch === '\r') s.lines.push('$ ');
        else s.lines[s.lines.length - 1] += ch;
      }
    });
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
