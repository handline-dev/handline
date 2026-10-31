// What waits on the person inside one agent's chat: an ask of this agent (the task to answer, who asked, the first lines) and the agent
// blocked until the person decides. Each is one row of the same shape as a task event, with the one button that does it.
import { TriangleAlert } from 'lucide-react';
import type { Card } from '@handline/app-core';
import { openTask } from '@handline/app-core';
import { useMyTurn, useTask } from '@handline/app-data';
import { Said } from '#src/ChatTaskCards.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { Button } from '#src/ui/index.ts';

const TITLE = 'min-w-0 cursor-pointer truncate border-0 bg-transparent p-0 text-left font-semibold text-ink';
const TNUM = 'cursor-pointer border-0 bg-transparent p-0 text-xs text-ink-2 underline';

function AskRow(props: { task: number; title: string; kind: string; line: string; said?: string; button: string }) {
  const { apply } = useWorkspace();
  const open = () => apply((w) => openTask(w, props.task));
  return (
    <div className="card-ask flex items-start gap-2 px-3 py-1 text-md" data-kind={props.kind} data-task={props.task}>
      <TriangleAlert size={16} className="mt-[3px] flex-none text-wait" aria-label="Needs you" />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-w-0 items-baseline gap-x-2">
          <button type="button" className={TITLE} onClick={open}>
            {props.title}
          </button>
          <button type="button" className={TNUM} onClick={open}>{`T${props.task}`}</button>
        </div>
        <span className="text-ink">
          <b className="text-wait">Needs you</b> <span className="text-ink-2">{props.line}</span>
        </span>
        {props.said ? <Said text={props.said} /> : null}
        <div className="mt-1">
          <Button variant="solid" label={`${props.button} T${props.task}`} onClick={open}>
            {props.button}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** The asks of this agent that wait on the person: Answer opens the task beside the chat. */
export function AskCards({ agent }: { agent: string }) {
  const { cards } = useMyTurn();
  const mine = cards.filter((c: Card) => c.kind === 'decision' && c.agent === agent && c.task != null);
  return (
    <>
      {mine.map((c) => (
        <AskRow
          key={c.key}
          task={c.task!}
          title={c.title}
          kind="ask"
          line={`asked by ${c.from ?? agent}`}
          said={c.detail}
          button="Answer"
        />
      ))}
    </>
  );
}

/** The agent is blocked by a task that is the person's: Decide opens that task. */
export function BlockedOnYou({ agent, task }: { agent: string; task: number }) {
  const t = useTask(task).data;
  return <AskRow task={task} title={t?.title ?? `T${task}`} kind="blocked" line={`${agent} is blocked until you decide`} button="Decide" />;
}
