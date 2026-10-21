// The marks editor of the capture dialog: the picture with an SVG over it and no toolbar. A tap pins a circle, a drag draws a box, going round
// something marks an area; the memo box opens at the mark, a red number on the mark jumps back to its memo. Each change exports the marked PNG
// (the marks that have words) and gives the marks to the dialog, which writes the message (`markText`).
import { useEffect, useReducer, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import {
  MARK_COLOR,
  exportAnnotated,
  initialMarks,
  keptMarks,
  marksReducer,
  marksToShapes,
  shapesToSvg,
  type Bitmap,
  type Mark,
} from '@handline/app-core';
import { Button, Textarea } from '#src/ui/index.ts';

/** The picture with the shapes drawn into its pixels, as a PNG. Null where there is no canvas (a test DOM). */
async function marked(img: HTMLImageElement, shapes: Parameters<typeof exportAnnotated>[1]): Promise<Blob | null> {
  const c = document.createElement('canvas');
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const g = c.getContext('2d');
  if (!g) return null;
  g.drawImage(img, 0, 0);
  const src = g.getImageData(0, 0, c.width, c.height);
  const out = exportAnnotated({ width: src.width, height: src.height, data: src.data } as Bitmap, shapes);
  g.putImageData(new ImageData(out.data as Uint8ClampedArray<ArrayBuffer>, out.width, out.height), 0, 0);
  return new Promise((ok) => c.toBlob(ok, 'image/png'));
}

export function Annotator({ image, onChange }: { image: Blob; onChange: (marked: Blob, marks: Mark[]) => void }): ReactNode {
  const [state, dispatch] = useReducer(marksReducer, undefined, initialMarks);
  const [src, setSrc] = useState('');
  const [size, setSize] = useState({ w: 1, h: 1 });
  const img = useRef<HTMLImageElement>(null);
  const memo = useRef<HTMLTextAreaElement>(null);
  const down = useRef(false); // the pointer is down on the picture (the state's draft lags a render behind fast events)
  useEffect(() => {
    const u = URL.createObjectURL(image);
    setSrc(u);
    return () => URL.revokeObjectURL(u);
  }, [image]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.key.toLowerCase() !== 'z') return;
      if (e.target instanceof Element && e.target.closest('input, textarea, select, [contenteditable]')) return; // the field's own undo
      e.preventDefault();
      dispatch({ type: 'undo' });
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, []);
  useEffect(() => void (state.open !== null && memo.current?.focus()), [state.open]);

  // Lines are in image pixels: a wide capture gets a thicker line so it shows at the size the dialog draws it.
  const line = Math.max(3, Math.round(size.w / 250));
  const { marks, open } = state;
  const sendOn = useRef(onChange);
  sendOn.current = onChange;
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    if (!keptMarks(marks).length) return void sendOn.current(image, marks);
    void marked(img.current!, marksToShapes(marks, line)).then(
      (b) => mine === seq.current && sendOn.current(b ?? image, marks), // no canvas: the words still go, with the picture as it was
      () => undefined,
    );
  }, [marks, image, line]);

  const at = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * size.w, y: ((e.clientY - r.top) / r.height) * size.h };
  };
  const draw = shapesToSvg(marksToShapes(marks, line, true), size.w, size.h);
  const pct = (x: number, y: number) => ({ left: `${(x / size.w) * 100}%`, top: `${(y / size.h) * 100}%` });
  const current = marks.find((m) => m.id === open);
  const path = state.draft?.map((p) => `${p.x},${p.y}`).join(' ');

  return (
    <div className="annotator flex min-h-0 min-w-0 flex-1 flex-col gap-2">
      <div className="flex items-center gap-3 text-ink-2">
        <span className="min-w-0 flex-1">Tap to pin, drag to box, draw around to mark an area</span>
        <span>
          {marks.length} {marks.length === 1 ? 'comment' : 'comments'}
        </span>
        <Button icon="undo" label="Undo (⌘Z)" disabled={!state.past.length} onClick={() => dispatch({ type: 'undo' })} />
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <div className="relative max-h-full max-w-full">
          <img
            ref={img}
            src={src}
            alt="The capture"
            data-testid="capture-preview"
            className="block max-h-[calc(100vh_-_7.5rem)] max-w-full select-none"
            draggable={false}
            onLoad={() => setSize({ w: img.current!.naturalWidth || 1, h: img.current!.naturalHeight || 1 })}
          />
          <svg
            className="absolute inset-0 h-full w-full cursor-crosshair touch-none"
            viewBox={draw.viewBox}
            aria-label="Mark here"
            onPointerDown={(e) => {
              const k = size.w / (e.currentTarget.getBoundingClientRect().width || size.w);
              dispatch({ type: 'scale', slop: Math.max(4, 6 * k), radius: Math.max(12, 14 * k) }); // a tap is a few screen pixels, whatever the picture's size
              dispatch({ type: 'open', id: null });
              e.currentTarget.setPointerCapture(e.pointerId);
              down.current = true;
              dispatch({ type: 'down', ...at(e) });
            }}
            onPointerMove={(e) => down.current && dispatch({ type: 'move', ...at(e) })}
            onPointerUp={(e) => {
              if (!down.current) return;
              down.current = false;
              dispatch({ type: 'up', ...at(e) });
            }}
            onPointerCancel={() => ((down.current = false), dispatch({ type: 'cancel' }))}
          >
            {draw.elements.map((el, i) =>
              el.type === 'path' ? (
                <path key={i} d={el.d} fill="none" stroke={el.stroke} strokeWidth={el.strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
              ) : null,
            )}
            {path ? <polyline points={path} fill="none" stroke={MARK_COLOR} strokeWidth={line} strokeDasharray={`${line * 2} ${line * 2}`} /> : null}
          </svg>
          {marks.map((m, i) => (
            <button
              key={m.id}
              type="button"
              aria-label={`Comment ${i + 1}${m.text.trim() ? `: ${m.text.trim()}` : ''}`}
              title={m.text}
              className="absolute grid size-5 -translate-x-1/2 -translate-y-1/2 cursor-pointer place-items-center rounded-full border-0 bg-bad p-0 text-label text-canvas"
              style={pct(m.rect.x, m.rect.y)}
              onClick={() => dispatch({ type: 'open', id: m.id })}
            >
              {i + 1}
            </button>
          ))}
          {current ? (
            // At the mark: below and to the right of it, flipped when that would leave the picture.
            <div
              className="absolute z-10 flex w-56 gap-1 rounded-card border border-bad bg-panel p-1.5 shadow-[var(--shadow-2)]"
              style={{
                ...pct(current.rect.x + current.rect.w, current.rect.y + current.rect.h),
                transform: `translate(${(current.rect.x + current.rect.w) / size.w > 0.6 ? 'calc(-100% - 8px)' : '8px'}, ${(current.rect.y + current.rect.h) / size.h > 0.7 ? 'calc(-100% - 8px)' : '8px'})`,
              }}
            >
              <Textarea
                ref={memo}
                rows={2}
                aria-label="Comment"
                placeholder="Type a comment…"
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
              <Button icon="close" label="Delete comment" onClick={() => dispatch({ type: 'remove', id: current.id })} />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
