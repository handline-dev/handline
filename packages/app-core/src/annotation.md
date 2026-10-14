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
- `exportAnnotated(source, shapes)` → a new Bitmap with shapes drawn; `drawShapes(bitmap, shapes)` draws in place.
- Same arithmetic on both platforms, so the same shapes give the same pixels. Text uses a built-in 5x7 capital font
  (lowercase is drawn as capitals; unknown characters as a box).
- The app decodes the picture to RGBA and encodes the result to PNG itself (web: canvas getImageData / toBlob;
  phone: the image manipulator of its choice). Nothing here decodes or encodes.

## Not here
No view. A react-native-svg or SVG preview draws `shapes` from the same model.
