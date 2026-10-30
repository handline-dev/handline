// Command cards: a fenced sh, bash or shell block in a message is a command that can be copied or run. This is what both apps say about one:
// whether a fence is one, what is risky about it, and the line under the card that tells how its run went.
const COMMAND_LANGS = new Set(['sh', 'bash', 'shell']);

/** A fence's language, as written after the three backticks, names a command. */
export const isCommandLang = (lang: string): boolean => COMMAND_LANGS.has(lang.trim().toLowerCase());

const RISKS: [RegExp, string][] = [
  [
    /\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-r\s+-f|-f\s+-r|--recursive\b[^\n;|&]*--force|--force\b[^\n;|&]*--recursive)/i,
    'It deletes files and folders for good.',
  ],
  [/(^|[\s;&|(])sudo\b/, 'It runs with full rights on the computer.'],
  [/\bgit\s+push\b[^\n;|&]*(\s-f\b|--force)/, 'It overwrites history on the remote.'],
  [/\b(curl|wget)\b[^\n]*\|\s*(sudo\s+)?(ba|z)?sh\b/, 'It runs a script it downloads.'],
];

/** One plain line on why a command is risky to run, or null when nothing in it is. */
export function commandRisk(command: string): string | null {
  return RISKS.find(([re]) => re.test(command))?.[1] ?? null;
}

/** The line under a command card: "Running · 4s", "Done · exit 0 · 3s", "Failed · exit 1 · 3s" or "Stopped". `now` is ms since the epoch. */
export function runLine(
  run: { state: 'running' | 'done' | 'failed' | 'stopped'; exit?: number; seconds?: number; started_at: string },
  now: number,
): string {
  if (run.state === 'stopped') return 'Stopped';
  if (run.state === 'running') return `Running · ${Math.max(0, Math.round((now - Date.parse(run.started_at)) / 1000))}s`;
  return `${run.state === 'done' ? 'Done' : 'Failed'} · exit ${run.exit ?? '?'} · ${run.seconds ?? 0}s`;
}
