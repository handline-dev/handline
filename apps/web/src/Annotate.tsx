// The annotation editor of the capture dialog: the picture with an SVG over it to draw on (pen, arrow, box, circle, text), undo, redo and clear.
// The shapes are app-core's (image pixels, shared with the phone); each change exports the marked picture as a PNG through `onChange`.
import { useEffect, useReducer, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { ANNOTATION_COLORS, annotate, exportAnnotated, initialAnnotation, shapesToSvg, type AnnotationTool, type Bitmap } from '@handline/app-core';
import { Button } from './ui/index.ts';

const TOOLS: [AnnotationTool, string][] = [
  ['pen', 'Pen'],
  ['arrow', 'Arrow'],
  ['box', 'Box'],
  ['ellipse', 'Circle'],
  ['text', 'Text'],
];

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

export function Annotator({ image, onChange }: { image: Blob; onChange: (marked: Blob) => void }): ReactNode {
  const [state, dispatch] = useReducer(annotate, undefined, initialAnnotation);
  const [src, setSrc] = useState('');
  const [size, setSize] = useState({ w: 1, h: 1 });
  const [typing, setTyping] = useState<{ x: number; y: number; text: string } | null>(null);
  const img = useRef<HTMLImageElement>(null);
  const down = useRef(false); // a stroke is under the pointer (the state's draft lags a render behind fast events)
  useEffect(() => {
    const u = URL.createObjectURL(image);
    setSrc(u);
    return () => URL.revokeObjectURL(u);
  }, [image]);

  // Lines are drawn in image pixels: a wide capture gets a thicker line so it shows at the size the dialog draws it.
  const loaded = () => {
    const el = img.current!;
    setSize({ w: el.naturalWidth || 1, h: el.naturalHeight || 1 });
    dispatch({ type: 'width', width: Math.max(4, Math.round((el.naturalWidth || 0) / 200)) });
  };
  // Send the picture on with what is drawn: the original while nothing is.
  const shapes = state.shapes;
  const seq = useRef(0);
  const sendOn = useRef(onChange);
  sendOn.current = onChange;
  useEffect(() => {
    const mine = ++seq.current;
    if (!shapes.length) return sendOn.current(image);
    void marked(img.current!, shapes).then((b) => b && mine === seq.current && sendOn.current(b), () => undefined);
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

  return (
    <div className="annotator grid gap-2">
      <div className="flex flex-wrap items-center gap-1" role="toolbar" aria-label="Markup">
        {TOOLS.map(([tool, label]) => (
          <Button key={tool} variant={state.tool === tool ? 'outline' : 'ghost'} aria-pressed={state.tool === tool} label={label} onClick={() => dispatch({ type: 'tool', tool })} />
        ))}
        <span className="mx-1 flex gap-1" role="group" aria-label="Colour">
          {ANNOTATION_COLORS.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={`Colour ${c}`}
              aria-pressed={state.color === c}
              className="h-5 w-5 rounded-full border border-line-strong aria-pressed:outline-2 aria-pressed:outline-offset-1 aria-pressed:outline-current"
              style={{ background: c }}
              onClick={() => dispatch({ type: 'color', color: c })}
            />
          ))}
        </span>
        <Button label="Undo" disabled={!shapes.length} onClick={() => dispatch({ type: 'undo' })} />
        <Button label="Redo" disabled={!state.redo.length} onClick={() => dispatch({ type: 'redo' })} />
        <Button label="Clear" disabled={!shapes.length} onClick={() => dispatch({ type: 'clear' })} />
      </div>
      <div className="relative mx-auto max-h-[50vh] w-fit max-w-full">
        <img ref={img} src={src} alt="The capture" data-testid="capture-preview" className="block max-h-[50vh] max-w-full select-none" draggable={false} onLoad={loaded} />
        <svg
          className="absolute inset-0 h-full w-full cursor-crosshair touch-none"
          viewBox={draw.viewBox}
          aria-label="Draw here"
          onPointerDown={(e) => {
            const p = at(e);
            if (state.tool === 'text') return setTyping({ ...p, text: '' });
            e.currentTarget.setPointerCapture(e.pointerId);
            down.current = true;
            dispatch({ type: 'strokeStart', ...p });
          }}
          onPointerMove={(e) => down.current && dispatch({ type: 'strokeMove', ...at(e) })}
          onPointerUp={(e) => down.current && ((down.current = false), dispatch({ type: 'strokeEnd', ...at(e) }))}
          onPointerCancel={() => ((down.current = false), dispatch({ type: 'strokeCancel' }))}
        >
          {draw.elements.map((el, i) =>
            el.type === 'path' ? (
              <path key={i} d={el.d} fill="none" stroke={el.stroke} strokeWidth={el.strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
            ) : (
              <text key={i} x={el.x} y={el.y} fontSize={el.fontSize} fill={el.fill} dominantBaseline="hanging" fontFamily="monospace">
                {el.text}
              </text>
            ),
          )}
        </svg>
        {typing ? (
          <input
            autoFocus
            aria-label="Text on the picture"
            className="absolute rounded-ctl border border-line bg-canvas px-1 text-ink"
            style={{ left: `${(typing.x / size.w) * 100}%`, top: `${(typing.y / size.h) * 100}%` }}
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
  );
}
