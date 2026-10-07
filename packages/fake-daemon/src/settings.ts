// An agent's settings and the schedules: agent.set, schedules.list, schedule.add, schedule.edit, schedule.remove.
import type { Schedule } from '@handline/contract';
import { badRequest, Failure, refused } from './errors.ts';
import type { Call } from './handlers.ts';

/** What a new session starts with: every value a string, as `hl agent set` takes it. */
export const defaultSettings = (model: string, effort: string): Record<string, string> => ({
  model,
  effort,
  weight: 'heavy',
  repo: '',
  skip_permissions: 'off',
  browser: 'off',
  browser_sites: '',
  context_k: '300',
  idle_close: '',
  handoff_note: 'default',
  sandbox: 'on',
  mode: 'default',
});

/** The keys an agent may change on itself; the rest are the founder's. */
export const OWN_KEYS = new Set(['model', 'effort', 'context_k', 'handoff_note']);
const ONE_OF: Record<string, string[]> = {
  effort: ['low', 'medium', 'high', 'xhigh', 'max'],
  weight: ['light', 'heavy'],
  skip_permissions: ['on', 'off'],
  sandbox: ['on', 'off'],
  browser: ['off', 'chrome', 'playwright'],
  handoff_note: ['default', 'on', 'off'],
  mode: ['default', 'auto', 'acceptEdits', 'plan'],
};
const WHOLE = new Set(['context_k', 'idle_close']);

function check(key: string, v: unknown): string {
  if (typeof v !== 'string') throw badRequest(`${key} takes a string`, key);
  if (!(key in defaultSettings('', ''))) throw badRequest(`${key} is not an agent setting`, key);
  if (ONE_OF[key] && !ONE_OF[key].includes(v)) throw badRequest(`${key} is one of ${ONE_OF[key].join(', ')}`, key);
  if (WHOLE.has(key) && !/^[0-9]*$/.test(v)) throw badRequest(`${key} is a whole number`, key);
  if (key === 'context_k' && !v) throw badRequest('context_k is a whole number', key);
  if (key === 'model' && !v.trim()) throw badRequest('model is not empty', key);
  return v;
}

export const SETTINGS_HANDLERS: Record<string, (h: Call) => unknown> = {
  'agent.set': (h) => {
    const { c, p, actor, script, agent } = h;
    const s = agent();
    const values = p.values as Record<string, unknown> | undefined;
    if (!values || typeof values !== 'object' || !Object.keys(values).length)
      throw badRequest('values names the settings to change', 'values');
    const next = { ...s.settings };
    for (const [k, v] of Object.entries(values)) {
      if (c.caller.kind === 'agent' && !OWN_KEYS.has(k)) throw refused('role', `${k} is the founder's to set`);
      next[k] = check(k, v);
    }
    s.settings = next;
    script.control(s, actor, 'agent.set', `${actor.name} set ${Object.keys(values).join(', ')}`);
    return script.accepted('Saved.');
  },
  'schedules.list': (h) => ({ schedules: h.script.schedules }),
  'schedule.add': (h) => {
    const { p, script } = h;
    for (const k of ['target', 'cron', 'message'] as const) if (typeof p[k] !== 'string' || !p[k]) throw badRequest(`${k} is needed`, k);
    const schedule: Schedule = {
      id: ++script.scheduleId,
      target: p.target as string,
      cron: p.cron as string,
      message: p.message as string,
      next_run: null,
      last_run: null,
      set_by: h.actor.name,
    };
    script.schedules.push(schedule);
    return script.accepted('Added.', { schedule: schedule.id });
  },
  'schedule.edit': (h) => {
    const { p, script } = h;
    const found = script.schedules.find((x) => x.id === p.schedule);
    if (!found) throw new Failure('not_found', 'no such schedule', { what: 'schedule' });
    for (const k of ['target', 'cron', 'message'] as const) if (p[k] !== undefined) found[k] = String(p[k]);
    return script.accepted('Saved.');
  },
  'schedule.remove': (h) => {
    const { p, script } = h;
    const at = script.schedules.findIndex((x) => x.id === p.schedule);
    if (at < 0) throw new Failure('not_found', 'no such schedule', { what: 'schedule' });
    script.schedules.splice(at, 1);
    return script.accepted('Removed.');
  },
};
