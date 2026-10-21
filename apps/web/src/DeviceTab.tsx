// The Android or iOS tab: the picture of a running emulator, device or simulator, taken again and again, beside the chat. A click on the
// Android picture is a tap on the device; the iOS one can only be looked at and refreshed. Needs the desktop shell, which runs adb and simctl.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, MenuButton, MenuItem } from '#src/ui/index.ts';
import { PreviewAsk } from '#src/PreviewAsk.tsx';
import { shell } from '#src/shell.ts';

const EVERY = { android: 150, ios: 700 } as const; // ms between pictures: adb is quick, simctl about 1.4 a second
const RETRY = 3000;

const blobOf = (png: string) => new Blob([Uint8Array.from(atob(png), (c) => c.charCodeAt(0))], { type: 'image/png' });

export function DeviceTab({ agent, kind, hidden }: { agent: string; kind: 'android' | 'ios'; hidden: boolean }) {
  const api = shell('deviceShot');
  const tap = shell('deviceTap');
  const [shot, setShot] = useState<Blob | null>(null);
  const [src, setSrc] = useState('');
  const [why, setWhy] = useState('');
  const [marking, setMarking] = useState<Blob | null>(null);
  const [now, setNow] = useState(0);
  const latest = useRef<Blob | null>(null);
  const look = useCallback(async (): Promise<boolean> => {
    const r = await api!.deviceShot!(kind);
    if (!r.ok) {
      setWhy(r.message);
      return false;
    }
    setWhy('');
    latest.current = blobOf(r.png);
    setShot(latest.current);
    return true;
  }, [api, kind]);
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
  const name = kind === 'android' ? 'Android' : 'iOS';
  return (
    <div className="device-tab flex min-h-0 flex-col">
      <div className="flex flex-none items-center gap-1.5 border-b border-line px-2 py-1.5">
        <span className="flex-1 text-body text-ink-2">{why ? '' : kind === 'android' ? 'Click the picture to tap the device.' : 'A picture of the booted simulator.'}</span>
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
      <div className="device-body flex min-h-0 flex-1 items-center justify-center overflow-auto p-2">
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
            className="max-h-full max-w-full object-contain"
            draggable={false}
            style={kind === 'android' && tap ? { cursor: 'pointer' } : undefined}
            onClick={(e) => {
              if (kind !== 'android' || !tap) return;
              const r = e.currentTarget.getBoundingClientRect();
              void tap.deviceTap!({ x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height });
            }}
          />
        ) : (
          <p className="m-0 p-6 text-center text-ink-2">Looking for {kind === 'android' ? 'an Android device' : 'an iOS simulator'}…</p>
        )}
      </div>
    </div>
  );
}
