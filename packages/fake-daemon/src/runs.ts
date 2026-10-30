// Command runs: shell.open with a command and a block, drawn as ChatMessage.runs, or TranscriptItem.runs for a reply's block ("r<id>:<n>"). The fake runs nothing: a command ends at once when it is
// `true`, `false` or `exit N`, else it runs until its window is closed.
import type { CommandRun } from '@handline/contract';
import { Failure, invalidParams } from '#src/errors.ts';
import type { FakeChat } from '#src/chat.ts';

export interface RunParams {
  command: string;
  block: string;
}

/** The command and block of a shell.open, or undefined for a plain shell; one without the other is bad_request. */
export function runParams(p: Record<string, unknown>): RunParams | undefined {
  const { command, block } = p;
  if (command === undefined && block === undefined) return undefined;
  if (command === undefined) throw invalidParams('block needs a command', 'command');
  if (typeof command !== 'string' || command === '' || command.length > 4000)
    throw invalidParams('command is text, 1 to 4000 characters', 'command');
  if (block === undefined) throw invalidParams('a command needs the block it is for', 'block');
  if (typeof block !== 'string' || !/^r?[0-9]+:[0-9]+$/.test(block))
    throw invalidParams('block is "<message id>:<n>" or "r<reply item id>:<n>"', 'block');
  return { command, block };
}

function exitOf(command: string): number | undefined {
  const c = command.trim();
  if (c === 'true') return 0;
  if (c === 'false') return 1;
  const m = /^exit\s+(\d+)$/.exec(c);
  return m ? Number(m[1]) : undefined;
}

/** The reply item a "r<id>:<n>" block names; not_found when it is not one of agent's replies. undefined for a message's block. */
export function replyOf(chat: FakeChat, agent: string, r: RunParams) {
  if (!r.block.startsWith('r')) return undefined;
  const id = Number(r.block.slice(1).split(':')[0]);
  const item = chat.agentReply(agent, id);
  if (!item) throw new Failure('not_found', `no reply ${id} of ${agent}`, { what: 'item', name: String(id) });
  return item;
}

/** Starts the run in its message's or reply item's runs, replacing the one of the same block; a message not being there is no error. */
export function startRun(chat: FakeChat, agent: string, terminal: string, r: RunParams, now: () => string): void {
  const exit = exitOf(r.command);
  const run: CommandRun =
    exit === undefined
      ? { block: r.block, terminal, started_at: now(), state: 'running' }
      : { block: r.block, terminal, started_at: now(), state: exit === 0 ? 'done' : 'failed', exit, seconds: 0 };
  const item = replyOf(chat, agent, r);
  if (item) {
    chat.changeItem(agent, item, { runs: [...(item.runs ?? []).filter((x) => x.block !== r.block), run] });
    return;
  }
  let m;
  try {
    m = chat.find(Number(r.block.split(':')[0]));
  } catch {
    return;
  }
  chat.change(m, { runs: [...(m.runs ?? []).filter((x) => x.block !== r.block), run] });
}

/** A window closed: the run in it that has not ended is stopped. */
export function stopRun(chat: FakeChat, terminal: string): void {
  const stop = (runs?: CommandRun[]) =>
    runs?.map((x: CommandRun) => (x.terminal === terminal && x.state === 'running' ? { ...x, state: 'stopped' as const } : x));
  const r = chat.runningItem(terminal);
  if (r) chat.changeItem(r.agent, r.item, { runs: stop(r.item.runs) });
  const m = chat.running(terminal);
  if (m) chat.change(m, { runs: stop(m.runs) });
}
