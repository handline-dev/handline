# Annotation module (app-core)

Pure TypeScript, no DOM, no React Native. Coordinates are image pixels (origin top-left).

## State and actions (`annotation.ts`)
- `initialAnnotation()` → `AnnotationState { shapes, redo, draft, tool, color, width, next }`
- `annotate(state, action)` is a reducer; use it with `useReducer` or the store.
- Actions: `{type:'tool', tool:'pen'|'arrow'|'box'|'text'}`, `{type:'color', color:'#rrggbb'}`, `{type:'width', width}`,
  `{type:'strokeStart'|'strokeMove'|'strokeEnd', x, y}`, `{type:'strokeCancel'}`,
  `{type:'text', x, y, text}`, `{type:'undo'|'redo'|'clear'}`.
- Gesture wiring is plain callbacks: PanResponder / gesture-handler / pointer events call the three stroke actions.
  The text tool does not stroke: on a tap, ask for the words, then dispatch `text` with the tap point.
- Draw `state.shapes`, then `state.draft` (the stroke in progress, or null).
- `Shape` is `pen {points}` | `arrow {from,to}` | `box {from,to}` | `text {at,text,size}`, each with `id, color, width`.
- The tool cannot change while a stroke is down; a box/arrow tap without a drag adds nothing.

## Export (`annotation-raster.ts`)
- `Bitmap = { width, height, data: Uint8ClampedArray /* RGBA */ }`
- `exportAnnotated(source, shapes, scale = 1)` → a new Bitmap with shapes drawn; `drawShapes(bitmap, shapes, scale)` draws in place.
  Shapes stay in original-image px; `scale` = bitmap size / original size scales positions, widths and text.
- Same arithmetic on both platforms, so the same shapes give the same pixels. Text uses a built-in 5x7 capital font
  (lowercase is drawn as capitals; unknown characters as a box).
- The app decodes the picture to RGBA and encodes the result to PNG itself (web: canvas getImageData / toBlob;
  phone: the image manipulator of its choice). Nothing here decodes or encodes.

## Preview (`shapesToSvg`)
`shapesToSvg(shapes, width, height)` → `{ viewBox, elements }`: `path {d, stroke, strokeWidth}` (stroke, no fill, round caps/joins)
or `text {x, y, fontSize, fill, text}` (top-left anchored). No SVG library imported; the view maps elements to its own components.

## Memo marks (`marks.ts`)
The capture dialog's way of marking: no tools. `marksReducer(state, action)` with `down|move|up {x,y}` turns one gesture into one mark (a tap within `slop` is a `pin`
circle, a loop that returns near its start is an `area`, any other drag is a `box`) and opens its memo; `memo`, `open`, `remove`, `undo`, `cancel`, `scale` (size
of a tap and a pin for the picture). `keptMarks` drops marks with no words; `markText(note, marks)` is the message (note, `n. words` lines, a fenced `marks`
JSON block of `{n, shape, x, y, w, h, text}` in picture pixels); `marksToShapes(marks, lineWidth)` gives shapes for `exportAnnotated`/`shapesToSvg`.
