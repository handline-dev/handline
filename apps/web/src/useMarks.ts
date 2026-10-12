// The marked rows of a task list: a click marks or unmarks one, Shift-click marks the rows between it and the last one clicked, and all
// of the rows shown can be marked at once or cleared.
import { useState } from 'react';
import { rangeBetween } from '@handline/app-core';

export function useMarks(order: number[]) {
  const [marked, setMarked] = useState<ReadonlySet<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  return {
    marked,
    setMarked,
    toggle: (id: number, range = false) => {
      setMarked((m) => {
        const n = new Set(m);
        if (range) rangeBetween(order, anchor, id).forEach((i) => n.add(i));
        else if (!n.delete(id)) n.add(id);
        return n;
      });
      setAnchor(id);
    },
    toggleAll: () => setMarked((m) => (m.size === order.length ? new Set() : new Set(order))),
  };
}
