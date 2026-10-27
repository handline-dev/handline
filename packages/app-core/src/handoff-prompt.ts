// The handoff prompt card (Settings > General, web and phone): the text the agent is asked, what the agent reads of it, and the guards on a save.
import type { Settings } from '@handline/client';

/** settings.get also names the built-in text and the agents that set their own. */
export type HandoffSettings = Settings & { handoff_prompt_default?: string; handoff_prompt_own?: string[] };

export const MIN_PROMPT = 20;
const NOTE_FILE = "(the note's file)";

/** Line breaks join into one line, so a pasted paragraph saves. */
export const joinLines = (t: string): string => t.replace(/\s*[\r\n]+\s*/g, ' ').trim();

/** Why a text is not saved, or null; blank is the built-in. */
export const promptProblem = (t: string): string | null => {
  const s = joinLines(t);
  return s && s.length < MIN_PROMPT ? 'Too short to guide a note. Leave it blank for the built-in.' : null;
};

/** What goes to settings.set: blank and the built-in itself are both "" (the daemon's built-in), so the card keeps following it. */
export const promptToSave = (t: string, builtin: string | undefined): string => {
  const s = joinLines(t);
  return s === builtin ? '' : s;
};

/** The text as the agent reads it: {path} and {max} filled in, the path on its own line when the text has no {path}. */
export function promptPreview(text: string): string {
  const out = text.replaceAll('{path}', NOTE_FILE).replaceAll('{max}', '3000');
  return text.includes('{path}') ? out : `${out}\n${NOTE_FILE}`;
}

/** The warning under the box when agents ask for their own text, or null. */
export function ownTextWarning(own: readonly string[] | undefined): string | null {
  const n = own?.length ?? 0;
  return n
    ? `${n} ${n === 1 ? 'agent sets its' : 'agents set their'} own text, so ${n === 1 ? 'it ignores' : 'they ignore'} this one.`
    : null;
}
