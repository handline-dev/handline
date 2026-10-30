// The photo of the viewer on its scrim: fit to the room, zoom by wheel, trackpad or two fingers, a double click between fit and 100 percent, a
// drag to pan once zoomed. While marking, a drag draws a numbered mark instead (the marks are the marks editor's state, Annotate.tsx).
import { useEffect, useLayoutEffect, useRef, useState, type Dispatch, type PointerEvent, type ReactNode, type RefObject } from 'react';
import { marksToShapes, shapesToSvg, type MarksAction, type MarksState } from '@handline/app-core';
import { Button, Textarea } from '#src/ui/index.ts';

type View = { s: number; x: number; y: number };
const MAX_ZOOM = 8;
const NOTE_W = 256;

export interface StageProps {
  url: string;
  name: string;
  marking: boolean;
  marks: MarksState;
  dispatch: Dispatch<MarksAction>;
  image: RefObject<HTMLImageElement | null>;
  /** True when the view is zoomed in, for the Fit chip. */
  onZoomed: (zoomed: boolean) => void;
  fitRef: RefObject<(() => void) | null>;
  /** A press-and-release on the scrim beside the photo. */
  onScrim: () => void;
}

export function ViewerStage({ url, name, marking, marks, dispatch, image, onZoomed, fitRef, onScrim }: StageProps): ReactNode {
  const stage = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState({ w: 1, h: 1 });
  const [nat, setNat] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View | null>(null); // null is fit
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ moved: boolean; drawing: boolean; from?: { x: number; y: number }; pinch?: { d: number } }>({
    moved: false,
    drawing: false,
  });

  useLayoutEffect(() => {
    const el = stage.current;
    if (!el) return;
    const read = () => setRoom({ w: el.clientWidth || 1, h: el.clientHeight || 1 });
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = nat.w ? Math.min(room.w / nat.w, room.h / nat.h, 1) : 1;
  const at: View = view ?? { s: fit, x: (room.w - nat.w * fit) / 2, y: (room.h - nat.h * fit) / 2 };
  const zoomed = !!view && view.s > fit * 1.001;
  useEffect(() => onZoomed(zoomed), [zoomed, onZoomed]);
  useEffect(() => void (fitRef.current = () => setView(null)), [fitRef]);

  /** A view with the photo kept on the stage: centred on an axis where it is smaller than the room, else not leaving an edge. */
  const keep = (v: View): View | null => {
    const s = Math.min(MAX_ZOOM, Math.max(fit, v.s));
    if (s <= fit * 1.001) return null;
    const axis = (pos: number, size: number, box: number) => (size <= box ? (box - size) / 2 : Math.min(0, Math.max(box - size, pos)));
    return { s, x: axis(v.x, nat.w * s, room.w), y: axis(v.y, nat.h * s, room.h) };
  };
  const zoomTo = (s: number, px: number, py: number) =>
    setView(keep({ s, x: px - (px - at.x) * (s / at.s), y: py - (py - at.y) * (s / at.s) }));
  const local = (e: { clientX: number; clientY: number }) => {
    const r = stage.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  // React's wheel listener is passive; the page must not scroll or zoom under a pinch.
  const wheel = useRef((_e: WheelEvent) => {});
  useEffect(() => {
    wheel.current = (e) => {
      e.preventDefault();
      const p = local(e);
      zoomTo(at.s * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.002)), p.x, p.y);
    };
  });
  useEffect(() => {
    const el = stage.current!;
    const on = (e: WheelEvent) => wheel.current(e);
    el.addEventListener('wheel', on, { passive: false });
    return () => el.removeEventListener('wheel', on);
  }, []);

  const natural = (e: PointerEvent) => {
    const p = local(e);
    return { x: (p.x - at.x) / at.s, y: (p.y - at.y) / at.s };
  };
  const down = (e: PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('[data-note]')) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = gesture.current;
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      g.pinch = { d: Math.hypot(a!.x - b!.x, a!.y - b!.y) || 1 };
      if (g.drawing) dispatch({ type: 'cancel' });
      g.drawing = false;
      g.moved = true;
      return;
    }
    g.moved = false;
    g.from = { x: e.clientX, y: e.clientY };
    if (marking && (e.target as HTMLElement).closest('[data-photo]')) {
      dispatch({ type: 'scale', slop: Math.max(4, 6 / at.s), radius: Math.max(12, 14 / at.s) });
      dispatch({ type: 'open', id: null });
      g.drawing = true;
      dispatch({ type: 'down', ...natural(e) });
    }
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const last = pointers.current.get(e.pointerId);
    if (!last) return;
    const g = gesture.current;
    const now = { x: e.clientX, y: e.clientY };
    pointers.current.set(e.pointerId, now);
    if (g.pinch && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a!.x - b!.x, a!.y - b!.y) || 1;
      const mid = local({ clientX: (a!.x + b!.x) / 2, clientY: (a!.y + b!.y) / 2 });
      zoomTo(at.s * (d / g.pinch.d), mid.x, mid.y);
      g.pinch.d = d;
      return;
    }
    if (g.from && Math.hypot(now.x - g.from.x, now.y - g.from.y) > 4) g.moved = true;
    if (g.drawing) dispatch({ type: 'move', ...natural(e) });
    else if (zoomed) setView(keep({ ...at, x: at.x + now.x - last.x, y: at.y + now.y - last.y }));
  };
  const up = (e: PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.delete(e.pointerId)) return;
    const g = gesture.current;
    if (pointers.current.size < 2) g.pinch = undefined;
    if (g.drawing) {
      g.drawing = false;
      dispatch({ type: 'up', ...natural(e) });
    }
  };
  const cancel = (e: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId);
    gesture.current.pinch = undefined;
    if (gesture.current.drawing) dispatch({ type: 'cancel' });
    gesture.current.drawing = false;
  };

  const line = Math.max(3, Math.round(nat.w / 250));
  const draw = shapesToSvg(marksToShapes(marks.marks, line, true), nat.w || 1, nat.h || 1);
  const path = marks.draft?.map((p) => `${p.x},${p.y}`).join(' ');
  const screen = (x: number, y: number) => ({ left: at.x + x * at.s, top: at.y + y * at.s });
  const current = marks.marks.find((m) => m.id === marks.open);
  // The note sits beside the mark's far corner, flipped to its left when that is where the room is, and kept inside the stage.
  const corner = current ? screen(current.rect.x + current.rect.w, current.rect.y + current.rect.h) : { left: 0, top: 0 };
  const noteLeft = Math.max(
    8,
    Math.min(room.w - NOTE_W - 8, corner.left + NOTE_W + 16 > room.w ? corner.left - NOTE_W - 8 : corner.left + 8),
  );
  const noteTop = Math.max(8, Math.min(room.h - 160, corner.top + 8));
  const memo = useRef<HTMLTextAreaElement>(null);
  useEffect(() => void (marks.open !== null && memo.current?.focus()), [marks.open]);

  return (
    <div
      ref={stage}
      className={`stage relative min-h-0 flex-1 touch-none select-none overflow-hidden ${marking ? 'cursor-crosshair' : zoomed ? 'cursor-grab' : ''}`}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
      onClick={(e) => {
        if (gesture.current.moved || (e.target as HTMLElement).closest('[data-photo], [data-note]')) return;
        onScrim();
      }}
      onDoubleClick={(e) => {
        if (marking || !(e.target as HTMLElement).closest('[data-photo]')) return;
        const p = local(e);
        if (zoomed) setView(null);
        else zoomTo(1, p.x, p.y);
      }}
    >
      <div
        data-photo
        className="absolute top-0 left-0 origin-top-left"
        style={{ width: nat.w || undefined, height: nat.h || undefined, transform: `translate(${at.x}px, ${at.y}px) scale(${at.s})` }}
      >
        <img
          ref={image}
          src={url}
          alt={name}
          draggable={false}
          className="block h-full w-full max-w-none rounded-sm"
          onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
        />
        {marking || marks.marks.length ? (
          <svg className="absolute inset-0 h-full w-full" viewBox={draw.viewBox} aria-hidden>
            {draw.elements.map((el, i) =>
              el.type === 'path' ? (
                <path
                  key={i}
                  d={el.d}
                  fill="none"
                  stroke={el.stroke}
                  strokeWidth={el.strokeWidth}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ) : null,
            )}
            {path ? (
              <polyline points={path} fill="none" stroke="#e5322d" strokeWidth={line} strokeDasharray={`${line * 2} ${line * 2}`} />
            ) : null}
          </svg>
        ) : null}
      </div>
      {marks.marks.map((m, i) => (
        <button
          key={m.id}
          type="button"
          data-note
          aria-label={`Note ${i + 1}${m.text.trim() ? `: ${m.text.trim()}` : ''}`}
          className="absolute grid size-5 -translate-x-1/2 -translate-y-1/2 cursor-pointer place-items-center rounded-full border-0 bg-bad p-0 text-xs text-white"
          style={screen(m.rect.x, m.rect.y)}
          onClick={() => dispatch({ type: 'open', id: m.id })}
        >
          {i + 1}
        </button>
      ))}
      {current ? (
        <div
          data-note
          className="absolute z-10 flex w-64 flex-col gap-1.5 rounded-md bg-panel p-2 shadow-[var(--shadow)]"
          style={{ left: noteLeft, top: noteTop }}
        >
          <label className="text-sm text-ink-2" htmlFor="viewer-note">
            What should change here?
          </label>
          <Textarea
            id="viewer-note"
            ref={memo}
            rows={2}
            value={current.text}
            onChange={(e) => dispatch({ type: 'memo', id: current.id, text: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                e.preventDefault();
                dispatch({ type: 'open', id: null });
              } else if (e.key === 'Escape') {
                e.stopPropagation();
                dispatch({ type: 'open', id: null });
              }
            }}
          />
          <div className="flex justify-end gap-1">
            <Button size="sm" label="Remove mark" onClick={() => dispatch({ type: 'remove', id: current.id })} />
            <Button size="sm" variant="outline" label="Done" onClick={() => dispatch({ type: 'open', id: null })} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
