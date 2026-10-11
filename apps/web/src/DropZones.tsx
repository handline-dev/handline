// The edges of a pane (or of the whole view) that a dragged tab can be dropped on to split.
import { useState } from 'react';
import { splitRoot, splitTab, type Edge } from '@handline/app-core';
import { useWorkspace } from './Workspace.tsx';

const EDGES: Edge[] = ['left', 'right', 'top', 'bottom'];
const BOX: Record<Edge, [pane: string, root: string]> = {
  left: ['inset-y-0 left-0 w-1/4', 'inset-y-0 left-0 w-[14px]'],
  right: ['inset-y-0 right-0 w-1/4', 'inset-y-0 right-0 w-[14px]'],
  top: ['inset-x-1/4 top-0 h-1/4', 'inset-x-0 top-0 h-[14px]'],
  bottom: ['inset-x-1/4 bottom-0 h-1/4', 'inset-x-0 bottom-0 h-[14px]'],
};
const OVER = 'bg-[color-mix(in_srgb,var(--primary)_22%,transparent)] outline-primary -outline-offset-2';

const zoneClass = (edge: Edge, root: boolean, over: boolean): string =>
  `dz${root ? ' root' : ''} ${edge} absolute ${root ? 'z-6' : 'z-4'} ${BOX[edge][root ? 1 : 0]}${over ? ` over ${OVER} ${root ? 'outline-3' : 'outline-2'}` : ''}`;

export function DropZones({ group, root }: { group?: string; root?: boolean }) {
  const { ws, dragging, drop } = useWorkspace();
  const [over, setOver] = useState<Edge | null>(null);
  return (
    <>
      {EDGES.map((edge) => (
        <div
          key={edge}
          className={zoneClass(edge, !!root, over === edge)}
          data-edge={edge}
          data-root={root ? 'true' : undefined}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(edge);
          }}
          onDragLeave={() => setOver(null)}
          onDrop={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setOver(null);
            if (dragging) drop(root ? splitRoot(ws, dragging, edge) : splitTab(ws, dragging, group!, edge));
          }}
        />
      ))}
    </>
  );
}
