// The Android or iOS tab: the picture of one running emulator, device or simulator, taken again and again, beside the chat. A picker lists
// the devices of its kind (adb, booted simulators); a click on the picture is a tap on the picked one, a drag a swipe, and typing on the
// picture is typed on the device. A simulator is driven through idb: without it the tab says how to install it and stays view-only.
// Needs the desktop shell, which runs adb, simctl and idb.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, MenuButton, MenuItem, Select } from '#src/ui/index.ts';
import { PreviewAsk } from '#src/PreviewAsk.tsx';
import { shell } from '#src/shell.ts';

const EVERY = { android: 150, ios: 700 } as const; // ms between pictures: adb is quick, simctl about 1.4 a second
const RETRY = 3000;

const IDB_HOW = 'Taps and typing on an iOS simulator need idb: brew install facebook/fb/idb-companion && pip3 install fb-idb';
const DRAG = 0.02; // a pointer that moved less than this fraction of the picture is a click, not a swipe
const TYPE_AFTER = 250; // ms of quiet before the typed letters go to the device

const blobOf = (png: string) => new Blob([Uint8Array.from(atob(png), (c) => c.charCodeAt(0))], { type: 'image/png' });

type Device = { kind: 'android' | 'ios'; id: string; name: string };

export function DeviceTab({
  agent,
  kind,
  hidden,
  picked,
  onPick,
}: {
  agent: string;
  kind: 'android' | 'ios';
  hidden: boolean;
  /** The device this tab shows, kept in the tab; '' until one is picked. */
  picked?: string;
  onPick?: (id: string) => void;
}) {
  const api = shell('deviceShot');
  const tap = shell('deviceTap');
  const list = shell('deviceList');
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [idb, setIdb] = useState(true);
  const [shot, setShot] = useState<Blob | null>(null);
  const [src, setSrc] = useState('');
  const [why, setWhy] = useState('');
  const [said, setSaid] = useState('');
  const [marking, setMarking] = useState<Blob | null>(null);
  const [now, setNow] = useState(0);
  const latest = useRef<Blob | null>(null);
  const down = useRef<{ x: number; y: number } | null>(null);
  const swiped = useRef(false);
  const typed = useRef({ text: '', timer: 0 });
  const mine = (devices ?? []).filter((d) => d.kind === kind);
  const id = mine.some((d) => d.id === picked) ? picked! : (mine[0]?.id ?? '');
  const drives = kind === 'android' ? !!tap : !!tap && idb;
  useEffect(() => {
    if (!list || hidden) return;
    void list.deviceList!().then((r) => {
      if (!r.ok) return;
      setDevices(r.devices);
      setIdb(r.idb);
    });
  }, [list, hidden, now]);
  const look = useCallback(async (): Promise<boolean> => {
    const r = await api!.deviceShot!({ kind, id });
    if (!r.ok) {
      setWhy(r.message);
      return false;
    }
    setWhy('');
    latest.current = blobOf(r.png);
    setShot(latest.current);
    return true;
  }, [api, kind, id]);
  useEffect(() => {
    if (!api || hidden) return;
    let stopped = false;
    let timer = 0;
    const loop = async () => {
      const ok = await look();
      if (!stopped) timer = window.setTimeout(() => void loop(), ok ? EVERY[kind] : RETRY);
    };
    void loop();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [api, kind, hidden, look, now]);
  useEffect(() => {
    if (!shot) return setSrc('');
    const u = URL.createObjectURL(shot);
    setSrc(u);
    return () => URL.revokeObjectURL(u);
  }, [shot]);
  const fraction = (e: { clientX: number; clientY: number; currentTarget: Element }) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
  };
  /** The device's refusal, in a sentence, beside the picture. */
  const told = (r: { ok: true } | { ok: false; message: string }) => setSaid(r.ok ? '' : r.message);
  const flush = () => {
    const text = typed.current.text;
    typed.current.text = '';
    if (text && list?.deviceText) void list.deviceText({ kind, id, text }).then(told);
  };
  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(() => () => void (clearTimeout(typed.current.timer), flushRef.current()), []); // letters typed just before the tab closes still go
  const name = kind === 'android' ? 'Android' : 'iOS';
  return (
    <div className="device-tab flex min-h-0 flex-col">
      <div className="flex flex-none flex-wrap items-center gap-1.5 border-b border-line px-2 py-1.5">
        {mine.length ? (
          <Select
            label={`${name} device`}
            value={id}
            onChange={(e) => onPick?.(e.target.value)}
            options={mine.map((d) => ({ value: d.id, label: d.name }))}
          />
        ) : null}
        <span className="min-w-0 flex-1 text-body text-ink-2">
          {why
            ? ''
            : drives
              ? 'Click the picture to tap, drag to swipe, type letters and numbers to type.'
              : kind === 'ios' && !idb
                ? IDB_HOW
                : 'A picture of the device.'}
        </span>
        <Button size="sm" icon="refresh" label="Refresh" onClick={() => setNow((n) => n + 1)} />
        {shot ? (
          <Button size="sm" variant="outline" label="Mark an area to ask" onClick={() => setMarking(latest.current)}>
            Mark an area
          </Button>
        ) : null}
        {shot ? (
          <MenuButton icon="open" size="sm" variant="outline" label="Open outside" name="Open outside" trigger="Open outside">
            <MenuItem icon="open" onSelect={() => void window.open(URL.createObjectURL(shot), '_blank', 'noopener')}>
              Open the picture in the browser
            </MenuItem>
          </MenuButton>
        ) : null}
      </div>
      {marking ? <PreviewAsk agent={agent} shot={marking} onClose={() => setMarking(null)} /> : null}
      <div className="device-body flex min-h-0 flex-1 flex-col items-center justify-center overflow-auto p-2">
        {!api ? (
          <p className="m-0 p-6 text-center text-ink-2">The {name} tab needs the desktop app.</p>
        ) : why ? (
          <p role="status" className="m-0 p-6 text-center text-ink-2">
            {why}
          </p>
        ) : src ? (
          <img
            src={src}
            alt={`${name} screen`}
            className="max-h-full max-w-full object-contain outline-none"
            draggable={false}
            tabIndex={drives ? 0 : undefined}
            style={drives ? { cursor: 'pointer' } : undefined}
            onPointerDown={(e) => void ((down.current = fraction(e)), (swiped.current = false))}
            onPointerUp={(e) => {
              const from = down.current;
              down.current = null;
              if (!drives || !from || !list?.deviceSwipe) return;
              const to = fraction(e);
              if (Math.hypot(to.x - from.x, to.y - from.y) < DRAG) return;
              swiped.current = true; // the click that follows is the end of this drag
              void list.deviceSwipe({ kind, id, x1: from.x, y1: from.y, x2: to.x, y2: to.y }).then(told);
            }}
            onClick={(e) => {
              if (!drives || swiped.current) return void (swiped.current = false);
              const at = fraction(e);
              void tap!.deviceTap!({ kind, id, ...at }).then(told);
            }}
            onBlur={() => void (clearTimeout(typed.current.timer), flush())}
            onKeyDown={(e) => {
              if (!drives || e.key.length !== 1 || e.metaKey || e.ctrlKey || e.altKey) return;
              e.preventDefault();
              typed.current.text += e.key;
              clearTimeout(typed.current.timer);
              typed.current.timer = window.setTimeout(flush, TYPE_AFTER);
            }}
          />
        ) : (
          <p className="m-0 p-6 text-center text-ink-2">Looking for {kind === 'android' ? 'an Android device' : 'an iOS simulator'}…</p>
        )}
        {said ? (
          <p role="alert" className="m-0 mt-2 text-center text-hint text-ink-2">
            {said}
          </p>
        ) : null}
      </div>
    </div>
  );
}
