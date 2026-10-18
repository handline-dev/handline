// The annotation editor of the capture dialog: the picture with an SVG over it to draw on. Tools: Arrow (default), Rectangle, Pen, Text and Number
// (keys A R P T N); a Number is a numbered ring with its words typed at the spot. Each change exports the marked PNG and the numbers' words via `onChange`.
import { useEffect, useReducer, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import {
  ANNOTATION_COLORS,
  annotate,
  exportAnnotated,
  initialAnnotation,
  noteMark,
  notesOf,
  shapesToSvg,
  type AnnotationTool,
  type Bitmap,
} from '@handline/app-core';
import { Button, Textarea, iconLabel, type IconName } from './ui/index.ts';

const TOOLS: [AnnotationTool, IconName, string][] = [
  ['arrow', 'arrow', 'A'],
  ['box', 'box', 'R'],
  ['pen', 'pen', 'P'],
  ['text', 'textTool', 'T'],
  ['note', 'number', 'N'],
];
const SWATCHES = ANNOTATION_COLORS.slice(0, 5);

/** A note being written (or changed: `id`), at the end of the click or drag that placed it. */
type Popover = { from: { x: number; y: number }; to: { x: number; y: number }; id?: number; text: string };

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

export function Annotator({ image, onChange }: { image: Blob; onChange: (marked: Blob, notes: string[]) => void }): ReactNode {
  const [state, dispatch] = useReducer(annotate, undefined, () => ({ ...initialAnnotation(), tool: 'arrow' as const }));
  const [palette, setPalette] = useState(false);
  const [src, setSrc] = useState('');
  const [size, setSize] = useState({ w: 1, h: 1 });
  const [typing, setTyping] = useState<{ x: number; y: number; text: string } | null>(null);
  const [pop, setPop] = useState<Popover | null>(null);
  const img = useRef<HTMLImageElement>(null);
  const down = useRef(false); // a stroke is under the pointer (the state's draft lags a render behind fast events)
  const start = useRef({ x: 0, y: 0 });
  useEffect(() => {
    const u = URL.createObjectURL(image);
    setSrc(u);
    return () => URL.revokeObjectURL(u);
  }, [image]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.target instanceof Element && e.target.closest('input, textarea, select, [contenteditable]')) return; // typing: the letters are words
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        dispatch({ type: e.shiftKey ? 'redo' : 'undo' });
      } else if (!e.metaKey && !e.ctrlKey && !e.altKey) {
        const hit = TOOLS.find(([, , k]) => k.toLowerCase() === e.key.toLowerCase());
        if (hit) dispatch({ type: 'tool', tool: hit[0] });
      }
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, []);

  // Lines are drawn in image pixels: a wide capture gets a thicker line so it shows at the size the dialog draws it.
  const loaded = () => {
    const el = img.current!;
    setSize({ w: el.naturalWidth || 1, h: el.naturalHeight || 1 });
    dispatch({ type: 'width', width: Math.max(4, Math.round((el.naturalWidth || 0) / 200)) });
  };
  // Send the picture on with what is drawn: the original while nothing is.
  const shapes = state.shapes;
  const notes = notesOf(shapes);
  const seq = useRef(0);
  const sendOn = useRef(onChange);
  sendOn.current = onChange;
  useEffect(() => {
    const mine = ++seq.current;
    const words = notesOf(shapes).map((n) => n.text);
    if (!shapes.length) {
      sendOn.current(image, words);
      return;
    }
    void marked(img.current!, shapes).then(
      (b) => mine === seq.current && sendOn.current(b ?? image, words), // no canvas: the words still go, with the picture as it was
      () => undefined,
    );
  }, [shapes, image]);

  const at = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * size.w, y: ((e.clientY - r.top) / r.height) * size.h };
  };
  const draw = shapesToSvg(state.draft ? [...shapes, state.draft] : shapes, size.w, size.h);
  const commitText = () => {
    if (typing) dispatch({ type: 'text', x: typing.x, y: typing.y, text: typing.text });
    setTyping(null);
  };
  const saveNote = () => {
    if (pop)
      dispatch(
        pop.id === undefined
          ? { type: 'note', from: pop.from, to: pop.to, text: pop.text }
          : { type: 'noteText', id: pop.id, text: pop.text },
      );
    setPop(null);
  };
  const pct = (p: { x: number; y: number }) => ({ left: `${(p.x / size.w) * 100}%`, top: `${(p.y / size.h) * 100}%` });

  return (
    <div className="annotator flex min-h-0 min-w-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1" role="toolbar" aria-label="Markup">
        {TOOLS.map(([tool, icon, k]) => (
          <Button
            key={tool}
            icon={icon}
            variant={state.tool === tool ? 'outline' : 'ghost'}
            aria-pressed={state.tool === tool}
            label={`${iconLabel(icon)} (${k})`}
            onClick={() => dispatch({ type: 'tool', tool })}
          />
        ))}
        <span className="relative mx-1">
          <button
            type="button"
            aria-label="Colour"
            title="Colour"
            aria-expanded={palette}
            className="block h-5 w-5 cursor-pointer rounded-full border border-line-strong"
            style={{ background: state.color }}
            onClick={() => setPalette(!palette)}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && palette) {
                e.stopPropagation();
                setPalette(false);
              }
            }}
          />
          {palette ? (
            <span
              className="absolute left-0 top-full z-10 mt-1 flex gap-1 rounded-card border border-line bg-panel p-1.5 shadow-[var(--shadow-pop)]"
              role="group"
              aria-label="Colours"
            >
              {SWATCHES.map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-label={`Colour ${c}`}
                  aria-pressed={state.color === c}
                  className="h-5 w-5 cursor-pointer rounded-full border border-line-strong aria-pressed:outline-2 aria-pressed:outline-offset-1 aria-pressed:outline-current"
                  style={{ background: c }}
                  onClick={() => {
                    dispatch({ type: 'color', color: c });
                    setPalette(false);
                  }}
                />
              ))}
            </span>
          ) : null}
        </span>
        <Button icon="undo" label="Undo (⌘Z)" disabled={!state.past.length} onClick={() => dispatch({ type: 'undo' })} />
        <Button icon="redo" label="Redo (⇧⌘Z)" disabled={!state.redo.length} onClick={() => dispatch({ type: 'redo' })} />
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
            onLoad={loaded}
          />
          <svg
            className="absolute inset-0 h-full w-full cursor-crosshair touch-none"
            viewBox={draw.viewBox}
            aria-label="Draw here"
            onPointerDown={(e) => {
              if (pop) return saveNote(); // a click away from the popover keeps what was typed
              if (state.tool === 'text') return; // the words are asked for at pointer up: a field opened here loses focus to the click that is still going on
              e.currentTarget.setPointerCapture(e.pointerId);
              down.current = true;
              start.current = at(e);
              if (state.tool !== 'note') dispatch({ type: 'strokeStart', ...start.current });
            }}
            onPointerMove={(e) => down.current && state.tool !== 'note' && dispatch({ type: 'strokeMove', ...at(e) })}
            onPointerUp={(e) => {
              if (state.tool === 'text') return setTyping({ ...at(e), text: '' });
              if (!down.current) return;
              down.current = false;
              if (state.tool === 'note') return setPop({ from: start.current, to: at(e), text: '' });
              dispatch({ type: 'strokeEnd', ...at(e) });
            }}
            onPointerCancel={() => ((down.current = false), dispatch({ type: 'strokeCancel' }))}
          >
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
              ) : (
                <text key={i} x={el.x} y={el.y} fontSize={el.fontSize} fill={el.fill} dominantBaseline="hanging" fontFamily="monospace">
                  {el.text}
                </text>
              ),
            )}
          </svg>
          {notes.map((n, i) => (
            <button
              key={n.id}
              type="button"
              aria-label={`Note ${i + 1}: ${n.text}`}
              title={n.text}
              className="absolute h-5 min-w-5 cursor-pointer border-0 bg-transparent p-0"
              style={pct(noteMark(n).label)}
              onClick={() => setPop({ from: n.from, to: n.to, id: n.id, text: n.text })}
            />
          ))}
          {pop ? (
            // Below and to the right of the spot, flipped when that would leave the picture.
            <div
              className="absolute z-10 w-56 rounded-card border border-line bg-panel p-1.5 shadow-[var(--shadow-pop)]"
              style={{
                ...pct(pop.to),
                transform: `translate(${pop.to.x / size.w > 0.6 ? 'calc(-100% - 8px)' : '8px'}, ${pop.to.y / size.h > 0.7 ? 'calc(-100% - 8px)' : '8px'})`,
              }}
            >
              <Textarea
                autoFocus
                rows={2}
                aria-label="Note"
                value={pop.text}
                onChange={(e) => setPop({ ...pop, text: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                    e.preventDefault();
                    saveNote();
                  } else if (e.key === 'Escape') {
                    e.stopPropagation();
                    setPop(null);
                  }
                }}
              />
            </div>
          ) : null}
          {typing ? (
            <input
              autoFocus
              aria-label="Text on the picture"
              className="absolute rounded-ctl border border-line bg-canvas px-1 text-ink"
              style={pct(typing)}
              value={typing.text}
              onChange={(e) => setTyping({ ...typing, text: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitText();
                else if (e.key === 'Escape') {
                  e.stopPropagation();
                  setTyping(null);
                }
              }}
              onBlur={commitText}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
