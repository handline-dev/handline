// The fields of an agent's settings page (the bridge's agent_settings.py), what each takes, and who may change it.
import type { AgentSettingValues } from '@handline/client';

export type FieldKey = keyof AgentSettingValues;
export type FieldKind = 'text' | 'number' | 'select' | 'switch' | 'sites' | 'model';

export interface Field {
  key: FieldKey;
  label: string;
  section: 'model' | 'work' | 'session' | 'browser';
  kind: FieldKind;
  options?: string[];
  /** Shown beside the control in muted text. */
  note?: string;
  unit?: string;
}

export const MODELS = ['opus', 'sonnet', 'haiku'];
export const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'];
export const MODES = ['default', 'auto', 'acceptEdits', 'plan'];
export const BROWSERS = ['off', 'chrome', 'playwright'];

export const FIELDS: Field[] = [
  { key: 'model', label: 'model', section: 'model', kind: 'model', options: MODELS },
  { key: 'effort', label: 'effort', section: 'model', kind: 'select', options: EFFORTS },
  { key: 'weight', label: 'weight', section: 'model', kind: 'select', options: ['light', 'heavy'] },
  { key: 'context_k', label: 'hand off at', section: 'model', kind: 'number', unit: 'k tokens' },
  { key: 'handoff_note', label: 'handoff note', section: 'model', kind: 'select', options: ['default', 'on', 'off'] },
  { key: 'repo', label: 'repo', section: 'work', kind: 'text' },
  { key: 'mode', label: 'mode', section: 'session', kind: 'select', options: MODES, note: 'from the next launch' },
  { key: 'sandbox', label: 'sandbox', section: 'session', kind: 'switch', note: 'off stays off until turned on again' },
  { key: 'browser', label: 'browser', section: 'browser', kind: 'select', options: BROWSERS, note: 'from the next launch' },
  { key: 'browser_sites', label: 'sites', section: 'browser', kind: 'sites' },
  { key: 'idle_close', label: 'idle close', section: 'browser', kind: 'number', unit: 'min, 0 = never' },
];

/** The keys an agent may change on itself; every other one is the founder's. */
export const OWN_KEYS: ReadonlySet<FieldKey> = new Set<FieldKey>(['model', 'effort', 'context_k', 'handoff_note']);

/** Whether this viewer may change the field: the founder any, anyone else only the agent's own keys. */
export const mayChange = (key: FieldKey, role: string | undefined): boolean => role === 'founder' || OWN_KEYS.has(key);

/** What the person typed for a field, or the problem in words; null is fine. Nothing invalid is sent. */
export function problemWith(f: Field, raw: string): string | null {
  if (f.kind === 'number') {
    if (!/^[0-9]+$/.test(raw.trim()))
      return f.key === 'idle_close' ? 'whole minutes, 0 for never' : 'a whole number of thousands of tokens';
    if (f.key === 'context_k' && Number(raw) < 1) return 'at least 1';
  }
  if (f.kind === 'model' && !raw.trim()) return 'a model name';
  return null;
}

/** The switch that asks first, and the words it asks in (the bridge's). Turning it the safe way asks nothing. */
export const DANGER: Partial<Record<FieldKey, { to: string; title: string; text: string; go: string }>> = {
  sandbox: {
    to: 'off',
    title: 'Run this agent without its sandbox',
    text: 'Its commands will run with nothing holding them back, until its session ends.',
    go: 'Turn off',
  },
};

const pad = (n: string) => n.padStart(2, '0');
/** A cron line in words for the common shapes; null for the rest. */
export function cronWords(cron: string): string | null {
  const m = cron.trim().match(/^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5)$/);
  if (!m) return null;
  return `every ${m[3] === '*' ? 'day' : 'weekday'} ${pad(m[2]!)}:${pad(m[1]!)}`;
}

/** The unit shown with the field; what follows its comma ("min, 0 = never") is the hint. */
export const fieldUnit = (f: Field): string | undefined => f.unit?.split(',')[0]?.trim();
