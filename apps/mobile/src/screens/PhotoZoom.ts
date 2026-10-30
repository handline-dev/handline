// The touch of the photo viewer: pinch to zoom, one finger to pan a zoomed photo, a double tap between fit and 100 percent. At fit one finger
// drags the photo: down far enough leaves, sideways far enough steps. While marking, one finger draws (`stroke`) and two still zoom.
// The gesture is a plain object, not hook state, so that its handlers read the latest input without a ref in render. Points are relative to the middle of the stage; the viewer turns them into image pixels.
import { useEffect, useState } from 'react';
import { PanResponder, type GestureResponderEvent } from 'react-native';

export interface Pt {
  x: number;
  y: number;
}
/** How the photo sits in the stage: scale 1 is fit, and x/y move it from the middle. */
export interface View {
  s: number;
  x: number;
  y: number;
}
export const FIT: View = { s: 1, x: 0, y: 0 };

export const MAX_ZOOM = 8;
const TAP_MS = 300;
const SLOP = 8;
const LEAVE = 120;
const STEP = 60;

export interface Stroke {
  down(p: Pt): void;
  move(p: Pt): void;
  up(p: Pt): void;
  cancel(): void;
}

export interface ZoomIn {
  room: { w: number; h: number };
  shown: { w: number; h: number };
  /** The scale where one photo pixel is one device pixel. */
  full: number;
  /** The stage's top left in the window. */
  origin: { x: number; y: number };
  marking: boolean;
  stroke: Stroke;
  onSwipe: (to: 'down' | 'left' | 'right') => void;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** The photo may not leave a gap at an edge, and sits in the middle where it is smaller than the stage. */
function bounded(v: View, i: ZoomIn): View {
  const mx = Math.max(0, (i.shown.w * v.s - i.room.w) / 2);
  const my = Math.max(0, (i.shown.h * v.s - i.room.h) / 2);
  return { s: v.s, x: clamp(v.x, -mx, mx), y: clamp(v.y, -my, my) };
}

interface Touch {
  pageX: number;
  pageY: number;
}
const middle = (ts: readonly Touch[]) => ({ x: (ts[0]!.pageX + ts[1]!.pageX) / 2, y: (ts[0]!.pageY + ts[1]!.pageY) / 2 });
const apart = (ts: readonly Touch[]) => Math.hypot(ts[0]!.pageX - ts[1]!.pageX, ts[0]!.pageY - ts[1]!.pageY);

/** One touch session's bookkeeping and the view it drives; `sync` hands it the viewer's current numbers after each render. */
class Gesture {
  handlers;
  private i!: ZoomIn;
  private view: View = FIT;
  private mode: 'one' | 'pinch' = 'one';
  private from: Pt = { x: 0, y: 0 }; // where the finger went down
  private at: Pt = { x: 0, y: 0 }; // where it is
  private v0: View = FIT;
  private d0 = 1;
  private mid0: Pt = { x: 0, y: 0 };
  private t0 = 0;
  private moved = false;
  private axis: 'x' | 'y' | null = null;
  private drawn = false; // a stroke is under way
  private lastTap = { t: 0, x: 0, y: 0 };

  constructor(private show: (v: View) => void) {
    this.handlers = PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: (e) => this.grant(e),
      onPanResponderMove: (e) => this.move(e),
      onPanResponderRelease: (e) => this.release(e),
      onPanResponderTerminate: () => this.terminate(),
    }).panHandlers;
  }

  sync(i: ZoomIn) {
    this.i = i;
  }

  set(v: View) {
    this.view = v;
    this.show(v);
  }

  private rel(x: number, y: number): Pt {
    const { origin, room } = this.i;
    return { x: x - origin.x - room.w / 2, y: y - origin.y - room.h / 2 };
  }

  private grant(e: GestureResponderEvent) {
    const p = this.rel(e.nativeEvent.pageX, e.nativeEvent.pageY);
    Object.assign(this, { mode: 'one', from: p, at: p, v0: this.view, t0: Date.now(), moved: false, axis: null, drawn: false });
    if (this.i.marking && e.nativeEvent.touches.length < 2) {
      this.drawn = true;
      this.i.stroke.down(p);
    }
  }

  private move(e: GestureResponderEvent) {
    const ts = e.nativeEvent.touches;
    if (ts.length >= 2) return this.pinch(ts);
    const p = this.rel(e.nativeEvent.pageX, e.nativeEvent.pageY);
    if (this.mode === 'pinch') Object.assign(this, { mode: 'one', from: p, v0: this.view, axis: null }); // a finger came off: pan from here, not a jump
    this.at = p;
    const dx = p.x - this.from.x;
    const dy = p.y - this.from.y;
    if (Math.hypot(dx, dy) > SLOP) this.moved = true;
    if (this.drawn) return this.i.stroke.move(p);
    if (this.v0.s > 1) return this.set(bounded({ s: this.v0.s, x: this.v0.x + dx, y: this.v0.y + dy }, this.i));
    if (!this.moved) return;
    this.axis ??= Math.abs(dy) > Math.abs(dx) ? 'y' : 'x';
    this.set({ s: 1, x: this.axis === 'x' ? dx : 0, y: this.axis === 'y' ? dy : 0 });
  }

  private pinch(ts: readonly Touch[]) {
    const m = middle(ts);
    const mid = this.rel(m.x, m.y);
    if (this.mode !== 'pinch') {
      if (this.drawn) this.i.stroke.cancel();
      Object.assign(this, { mode: 'pinch', drawn: false, v0: this.view, d0: Math.max(1, apart(ts)), mid0: mid });
    }
    const s = clamp(this.v0.s * (apart(ts) / this.d0), 1, MAX_ZOOM);
    const q = { x: (this.mid0.x - this.v0.x) / this.v0.s, y: (this.mid0.y - this.v0.y) / this.v0.s }; // the photo's point that was under the fingers
    this.moved = true;
    this.set(bounded({ s, x: mid.x - s * q.x, y: mid.y - s * q.y }, this.i));
  }

  private release(e: GestureResponderEvent) {
    const v = this.view;
    if (this.drawn) {
      this.drawn = false;
      return this.i.stroke.up(this.rel(e.nativeEvent.pageX, e.nativeEvent.pageY));
    }
    if (this.mode === 'pinch') return v.s < 1.05 ? this.set(FIT) : undefined;
    if (!this.moved && Date.now() - this.t0 < TAP_MS) return this.tap(this.at);
    if (v.s !== 1) return;
    this.set(FIT);
    if (this.axis === 'y' && v.y > LEAVE) this.i.onSwipe('down');
    else if (this.axis === 'x' && v.x > STEP) this.i.onSwipe('right');
    else if (this.axis === 'x' && v.x < -STEP) this.i.onSwipe('left');
  }

  /** A second tap near the first goes between fit and 100 percent, around the tap. */
  private tap(p: Pt) {
    const last = this.lastTap;
    const twice = Date.now() - last.t < TAP_MS && Math.hypot(p.x - last.x, p.y - last.y) < 40;
    this.lastTap = twice ? { t: 0, x: 0, y: 0 } : { t: Date.now(), x: p.x, y: p.y };
    if (!twice) return;
    if (this.view.s > 1) return this.set(FIT);
    const s = clamp(this.i.full, 2, MAX_ZOOM);
    this.set(bounded({ s, x: p.x - s * p.x, y: p.y - s * p.y }, this.i));
  }

  private terminate() {
    if (this.drawn) this.i.stroke.cancel();
    this.drawn = false;
    if (this.view.s === 1) this.set(FIT);
  }
}

export function useZoom(input: ZoomIn) {
  const [view, setView] = useState<View>(FIT);
  const [gesture] = useState(() => new Gesture(setView));
  useEffect(() => gesture.sync(input));
  return { view, handlers: gesture.handlers, reset: () => gesture.set(FIT) };
}
