// What a dragged tab shows over a pane: one cover for the whole pane (strip included) that picks one zone from the pointer. The tab strip shows an
// insert line between tabs, the centre lights the whole pane, an edge lights the exact half the split will take (app-core drop.ts).
import { useRef, useState, type DragEvent, type RefObject } from 'react';
import { CHAT, dropPreview, dropZone, groupOf, moveTab, splitTab, type Box, type DropZone, type Group } from '@handline/app-core';
import { useWorkspace } from '#src/Workspace.tsx';

/** The index a tab dropped at `x` takes among the tabs of `group` (all of them, the ones a switch hides included), or -1 for the end; Chat stays first. */
function slotAt(strip: HTMLElement, group: Group, x: number): number {
  const mids = [...strip.querySelectorAll<HTMLElement>('[role=tab]')].map((el) => {
    const r = el.getBoundingClientRect();
    return r.left + r.width / 2;
  });
  const at = mids.findIndex((m) => x < m);
  return at >= 0 && group.tabs[0] === CHAT ? Math.max(at, 1) : at;
}

/** Where the insert line stands (px from the pane's left): before the tab at `at`, or after the last shown tab. */
function lineAt(strip: HTMLElement, at: number, from: number): number {
  const shown = [...strip.querySelectorAll<HTMLElement>('[role=tab]')].map((el) => el.getBoundingClientRect()).map((r, i) => ({ r, i })).filter(({ r }) => r.width > 0);
  const next = shown.find(({ i }) => at >= 0 && i >= at);
  const last = shown[shown.length - 1];
  return (next ? next.r.left : last ? last.r.right : strip.getBoundingClientRect().left) - from;
}

export function DropOverlay({ group, strip }: { group: Group; strip: RefObject<HTMLDivElement | null> }) {
  const { ws, dragging, apply, drop, setDragging, merged } = useWorkspace();
  const [shown, setShown] = useState<{ zone: DropZone; box: Box | null; line: number | null; size: { w: number; h: number } } | null>(null);
  const at = useRef(-1);
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
    const bar = strip.current;
    const size = { w: r.width, h: r.height };
    const zone = dropZone({ ...size, strip: bar?.getBoundingClientRect().height ?? 0 }, { x: e.clientX - r.left, y: e.clientY - r.top }, edgesOk);
    if (zone.kind === 'strip' && bar) {
      const i = slotAt(bar, group, e.clientX);
      const where = i < 0 ? group.tabs.length : i;
      at.current = i;
      const stays = same && (where === group.tabs.indexOf(dragging) || where === group.tabs.indexOf(dragging) + 1);
      return stays ? null : { zone, box: null, line: lineAt(bar, i, r.left), size };
    }
    if (zone.kind === 'center' && same) return null;
    return { zone, box: dropPreview(size, zone), line: null, size };
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
          apply((w) => moveTab(w, dragging, group.id, next.zone.kind === 'strip' && at.current >= 0 ? at.current : undefined));
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
      {shown?.line != null ? (
        <div className="drop-line pointer-events-none absolute top-1 w-0.5 rounded-full bg-primary" style={{ left: shown.line - 1, height: (strip.current?.getBoundingClientRect().height ?? 32) - 8 }} />
      ) : null}
    </div>
  );
}
