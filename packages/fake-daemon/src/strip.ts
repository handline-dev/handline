// The fake daemon's agent.strip: the numbers under an agent's header. An agent with an open session has a context and a prompt cache that
// has 240 seconds left; one with none has nulls. A test sets `session.strip` to change an agent's.
import type { Methods } from '@handline/contract';
import { Failure, invalidParams } from './errors.ts';
import type { Call } from './handlers.ts';

export type Strip = Methods['agent.strip']['result'];

const WORDS: Record<string, Strip['screen_word']> = { running: 'RUNNING', idle: 'IDLE', waiting: 'DIALOG', blocked: 'DIALOG' };

export const STRIP_HANDLERS: Record<string, (h: Call) => unknown> = {
  'agent.strip': ({ p, script }): Strip => {
    if (typeof p.agent !== 'string' || !p.agent) throw invalidParams('agent is an agent name', 'agent');
    const s = script.sessions.get(p.agent);
    if (!s) throw new Failure('not_found', `no agent named ${p.agent}`, { what: 'agent', name: p.agent });
    const open = s.open;
    return {
      agent: s.fx.name,
      model: s.fx.model,
      account: s.fx.account,
      ctx_pct: open ? 37 : null,
      ctx_used: open ? 74_000 : null,
      ctx_limit: open ? 200_000 : null,
      cache_left_s: open ? 240 : null,
      screen_word: open ? (WORDS[s.state] ?? 'IDLE') : null,
      screen_detail: open ? s.state : '',
      ...s.strip,
    };
  },
};
