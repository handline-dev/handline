// The one line under an agent's name in the list: what it is doing now, else the task it holds, else its last message.
import type { AgentView } from '@handline/client';
import { handoffLine } from '#src/chat.ts';
import { taskRef } from '#src/tasks.ts';

const base = (path: string): string => path.replace(/\/+$/, '').split('/').pop() || path;

/** A tool call (`Bash cd /x && ...`, `Edit /a/b.tsx`) as words; a text that is not one comes back with its absolute paths cut to their last part. */
export function doingWords(text: string): string {
  const [tool = '', ...rest] = text.trim().split(/\s+/);
  const arg = rest.join(' ');
  const file = arg ? base(rest[0]!) : '';
  switch (tool) {
    case 'Edit':
    case 'MultiEdit':
    case 'Write':
    case 'NotebookEdit':
      return file ? `Editing ${file}` : 'Editing a file';
    case 'Read':
      return file ? `Reading ${file}` : 'Reading a file';
    case 'Bash':
      return 'Running a command';
    case 'Grep':
    case 'Glob':
      return 'Searching the code';
    case 'Task':
    case 'Agent':
      return 'Running a subagent';
    case 'WebFetch':
    case 'WebSearch':
      return 'Searching the web';
    default:
      return text.replace(/(?:\/[\w.@-]+){2,}/g, (p) => base(p));
  }
}

/** A reminder the daemon wrote for the agent (`T3867 has waited 2h`), not words the agent said. */
const nudge = (line: string): boolean => /^T\d+\b.*\b(has waited|is waiting|still waiting)\b/i.test(line);

const firstLine = (text: string): string =>
  text
    .split('\n')
    .map((l) => l.trim())
    .find(Boolean) ?? '';

/** The line under the name: what it is doing now in words, else a request to the founder they have not seen, else the title of its held task, else its last message; '' when none. */
export function agentLine(a: AgentView): string {
  if (a.handoff) {
    const { why, what } = handoffLine(a.handoff);
    return `${why}: ${what.charAt(0).toLowerCase()}${what.slice(1)}`;
  }
  if (a.state === 'running' && a.doing?.text) return doingWords(a.doing.text);
  if (a.status?.text) return a.status.text;
  const last = firstLine(a.last_to_founder?.text ?? '');
  const say = last && !nudge(last) ? last : '';
  if (a.founder_unread && say) return say; // a request you have not seen outranks the task
  if (a.task) return `${taskRef(a.task.id)} ${a.task.title}`;
  return say;
}
