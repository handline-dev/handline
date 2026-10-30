// The agent page: one agent's workspace, or two side by side (the second rides in the route, `?beside=`). Dragging an agent from the sidebar
// shows two halves to drop it on: the right one opens it beside, the left one switches that pane (app-core's dropAgent).
import { useEffect, useRef, useState, type DragEvent } from 'react';
import { dropAgent, MIN_PANE, type Half, type KV } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { goSplit } from '#src/route.ts';
import { AgentWorkspace } from '#src/Workspace.tsx';

/** The data a dragged sidebar agent row carries. */
export const AGENT_DRAG = 'application/x-handline-agent';
const DIVIDER = 1;

export function SplitView({ agent, beside, daemon, storage }: { agent: string; beside: string; daemon: string; storage: KV | null }) {
  const store = useStore();
  const root = useRef<HTMLDivElement>(null);
  const [focus, setFocus] = useState<Half>('left'); // meaningful while a second agent is shown
  const [dragging, setDragging] = useState(false);
  const [over, setOver] = useState<Half | null>(null);
  useEffect(() => {
    const start = (e: globalThis.DragEvent) => setDragging(!!e.dataTransfer?.types?.includes(AGENT_DRAG));
    const end = () => (setDragging(false), setOver(null));
    addEventListener('dragstart', start);
    addEventListener('dragend', end);
    addEventListener('drop', end);
    return () => (removeEventListener('dragstart', start), removeEventListener('dragend', end), removeEventListener('drop', end));
  }, []);
  const drop = (half: Half, e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    setOver(null);
    const next = dropAgent({ agent, beside: beside || null }, half, e.dataTransfer.getData(AGENT_DRAG));
    if (!next) return;
    if (next.beside && !beside && (root.current?.clientWidth ?? Infinity) < 2 * MIN_PANE.w + DIVIDER) {
      store.fail('The window is too narrow for another pane');
      return;
    }
    setFocus(next.beside && half === 'right' ? 'right' : 'left');
    goSplit(next.agent, next.beside);
  };
  const half = (side: Half) => (
    <div
      className={`absolute inset-y-0 ${side === 'left' ? 'left-0' : 'right-0'} w-1/2 border-2 border-dashed ${over === side ? 'border-acc bg-[color-mix(in_srgb,var(--acc)_14%,transparent)]' : 'border-transparent'}`}
      data-drop-half={side}
      onDragOver={(e) => {
        if (!e.dataTransfer?.types?.includes(AGENT_DRAG)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        setOver(side);
      }}
      onDrop={(e) => drop(side, e)}
    />
  );
  const pane = (name: string, side: Half) => (
    <div
      key={name}
      className="half flex min-h-0 min-w-0 flex-1"
      data-half={side}
      onPointerDownCapture={() => setFocus(side)}
      onFocusCapture={() => setFocus(side)}
    >
      <AgentWorkspace
        agent={name}
        daemon={daemon}
        storage={storage}
        active={!beside || focus === side}
        onCloseSplit={side === 'right' ? () => goSplit(agent, null) : undefined}
      />
    </div>
  );
  return (
    <div className="agent-page relative flex min-h-0 min-w-0 flex-1" ref={root}>
      {pane(agent, 'left')}
      {beside ? (
        <>
          <div className="w-px flex-none bg-line" role="separator" aria-orientation="vertical" />
          {pane(beside, 'right')}
        </>
      ) : null}
      {dragging ? (
        <div className="absolute inset-0 z-40" data-dropping-agent>
          {half('left')}
          {half('right')}
        </div>
      ) : null}
    </div>
  );
}
