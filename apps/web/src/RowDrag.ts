// Dragging an agent row to a new place among its siblings (T5629 O4): the drop writes the person's order (NavPrefs.order). Only the tree
// layout takes it, and only between agents under the same parent (the top level within one team).
import { useRef, useState, type DragEvent, type HTMLAttributes } from 'react';
import { moveInOrder } from '@handline/app-core';
import type { AgentView } from '@handline/client';

type Place = { name: string; before: boolean };
export interface RowDrag {
  attrs: (agent: AgentView) => HTMLAttributes<HTMLDivElement>;
  /** Where the dragged row would land: a line above or below this row. */
  mark: (name: string) => 'before' | 'after' | null;
}

export function useRowDrag(
  on: boolean,
  order: readonly string[],
  /** The rows that stand beside `agent` under its parent, in the order shown. */
  siblingsOf: (agent: AgentView) => string[],
  save: (order: string[]) => void,
): RowDrag {
  const from = useRef<string | null>(null);
  const [over, setOver] = useState<Place | null>(null);
  const target = (e: DragEvent, agent: AgentView): Place | null => {
    const mine = from.current;
    if (!mine || mine === agent.name || !siblingsOf(agent).includes(mine)) return null;
    const box = e.currentTarget.getBoundingClientRect();
    return { name: agent.name, before: e.clientY < box.top + box.height / 2 };
  };
  const end = () => ((from.current = null), setOver(null));
  return {
    attrs: (agent) =>
      !on
        ? {}
        : {
            draggable: true,
            onDragStart: (e) => {
              from.current = agent.name;
              if (e.dataTransfer) {
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', agent.name);
              }
            },
            onDragOver: (e) => {
              const t = target(e, agent);
              if (!t) return;
              e.preventDefault();
              if (over?.name !== t.name || over.before !== t.before) setOver(t);
            },
            onDrop: (e) => {
              const t = target(e, agent);
              const mine = from.current;
              end();
              if (!t || !mine) return;
              e.preventDefault();
              const sibs = siblingsOf(agent);
              const rest = sibs.filter((n) => n !== mine);
              const at = rest.indexOf(t.name) + (t.before ? 0 : 1);
              save(moveInOrder(order, sibs, mine, at));
            },
            onDragEnd: end,
          },
    mark: (name) => (over?.name === name ? (over.before ? 'before' : 'after') : null),
  };
}
