// What a dragged tab shows over a pane: one cover for the whole pane (handle included) that picks one zone from the pointer. The handle lights up
// and takes the tab at the end of the pane, the centre lights the whole pane, an edge lights the exact half the split will take (app-core drop.ts).
import { useState, type DragEvent, type RefObject } from 'react';
import { dropPreview, dropZone, groupOf, moveTab, splitTab, type Box, type DropZone, type Group } from '@handline/app-core';
import { useWorkspace } from '#src/Workspace.tsx';

/** `handle` is the pane's thin handle while the window is split, null when the pane has none (its tabs are all in the bar). */
export function DropOverlay({ group, handle }: { group: Group; handle: RefObject<HTMLDivElement | null> | null }) {
  const { ws, dragging, apply, drop, setDragging, merged } = useWorkspace();
  const [shown, setShown] = useState<{ zone: DropZone; box: Box | null; size: { w: number; h: number } } | null>(null);
  if (!dragging) return null;
  const from = groupOf(ws, dragging);
  if (!from) return null;
  const same = from.id === group.id;
  const alone = from.tabs.length === 1;
  const edgesOk = !merged && !(alone && same);

  /** The zone under the pointer, or null where a drop would change nothing. */
  const pick = (e: DragEvent): NonNullable<typeof shown> | null => {
    const el = e.currentTarget as HTMLElement;
    const r = el.getBoundingClientRect();
    const bar = handle?.current;
    const size = { w: r.width, h: r.height };
    const zone = dropZone(
      { ...size, strip: bar?.getBoundingClientRect().height ?? 0 },
      { x: e.clientX - r.left, y: e.clientY - r.top },
      edgesOk,
    );
    if (zone.kind === 'strip') {
      const last = group.tabs.length - 1;
      const stays = same && group.tabs.indexOf(dragging) === last; // already the last tab: dropping on the handle changes nothing
      return stays ? null : { zone, box: { x: 0, y: 0, w: size.w, h: bar?.getBoundingClientRect().height ?? 0 }, size };
    }
    if (zone.kind === 'center' && same) return null;
    return { zone, box: dropPreview(size, zone), size };
  };

  return (
    <div
      className="drop-overlay absolute inset-0 z-20"
      onDragOver={(e) => {
        const next = pick(e);
        setShown(next);
        if (!next) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
      }}
      onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && setShown(null)}
      onDrop={(e) => {
        const next = pick(e);
        setShown(null);
        if (!next) return;
        e.preventDefault();
        e.stopPropagation();
        if (next.zone.kind === 'edge') drop(splitTab(ws, dragging, group.id, next.zone.edge));
        else {
          apply((w) => moveTab(w, dragging, group.id));
          setDragging(null);
        }
      }}
    >
      {shown?.box ? (
        <div
          className={`drop-preview pointer-events-none absolute rounded-card bg-[color-mix(in_srgb,var(--primary)_18%,transparent)] outline-2 -outline-offset-2 outline-primary transition-[left,top,width,height] duration-[120ms]`}
          data-zone={shown.zone.kind === 'edge' ? shown.zone.edge : shown.zone.kind}
          style={{ left: shown.box.x, top: shown.box.y, width: shown.box.w, height: shown.box.h }}
        />
      ) : null}
    </div>
  );
}
