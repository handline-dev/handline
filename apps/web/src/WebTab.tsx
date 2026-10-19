// A page of the agent's own app (its dev server), shown beside the chat. In the desktop shell it is a native view laid over this tab's
// body (the shell positions it from the rectangle reported here); in a plain browser it is an iframe.
import { useEffect, useRef, useState } from 'react';
import { Button, Input, MenuButton, MenuItem } from './ui/index.ts';
import { PreviewAsk } from './PreviewAsk.tsx';
import { shell } from './shell.ts';

/** What was typed as an address: `localhost:5173` is a page too. Null when it cannot be one. */
export function addressOf(typed: string): string | null {
  const t = typed.trim();
  if (!t) return null;
  const url = /^https?:\/\//i.test(t) ? t : `http://${t}`;
  try {
    return new URL(url).host ? url : null;
  } catch {
    return null;
  }
}

/** True while something in the page must draw over the preview: a dialog or a menu. */
const covered = (): boolean => !!document.querySelector('dialog[open], [aria-modal="true"], [role="menu"]');

export function WebTab({ agent, url, onUrl, hidden }: { agent: string; url: string; onUrl: (u: string) => void; hidden: boolean }) {
  const [typed, setTyped] = useState(url);
  const [reload, setReload] = useState(0);
  const body = useRef<HTMLDivElement>(null);
  const api = shell('preview');
  const grab = shell('capturePreview');
  const [shot, setShot] = useState<Blob | null>(null);
  const [problem, setProblem] = useState('');
  const mark = async () => {
    setProblem('');
    const r = await grab!.capturePreview!();
    if (!r.ok) return setProblem(r.message);
    setShot(new Blob([Uint8Array.from(atob(r.png), (c) => c.charCodeAt(0))], { type: 'image/png' }));
  };
  useEffect(() => setTyped(url), [url]);
  useEffect(() => {
    if (!api || !url || !body.current) return;
    const el = body.current;
    const place = () => {
      const r = el.getBoundingClientRect();
      const z = shell('zoom')?.zoom?.() || 1;
      void api.preview!(
        hidden || covered() || r.width < 1 || r.height < 1
          ? null
          : { url, x: r.left * z, y: r.top * z, width: r.width * z, height: r.height * z },
      );
    };
    place();
    const watch = new ResizeObserver(place);
    watch.observe(el);
    const doc = new MutationObserver(place);
    doc.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'aria-modal'] });
    addEventListener('resize', place);
    return () => {
      watch.disconnect();
      doc.disconnect();
      removeEventListener('resize', place);
      void api.preview!(null);
    };
  }, [api, url, hidden, reload]);
  const go = () => {
    const a = addressOf(typed);
    if (a && a !== url) onUrl(a);
    else if (a) setReload((n) => n + 1);
  };
  return (
    <div className="web-tab flex min-h-0 flex-col">
      <form
        className="flex flex-none items-center gap-1.5 border-b border-line px-2 py-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          go();
        }}
      >
        <Input aria-label="Address" placeholder="localhost:5173" value={typed} onChange={(e) => setTyped(e.target.value)} className="flex-1" />
        <Button type="submit" icon="refresh" size="sm" label={url ? 'Reload' : 'Show'} />
        {url && grab ? (
          <Button size="sm" variant="outline" label="Mark an area to ask" onClick={() => void mark()}>
            Mark an area
          </Button>
        ) : null}
        {url ? (
          <MenuButton icon="open" size="sm" variant="outline" label="Open outside" name="Open outside" trigger="Open outside">
            <MenuItem icon="open" onSelect={() => void window.open(url, '_blank', 'noopener')}>
              Open in the browser
            </MenuItem>
          </MenuButton>
        ) : null}
      </form>
      {problem ? (
        <p className="m-0 border-b border-line px-3 py-1.5 text-body text-bad" role="alert">
          {problem}
        </p>
      ) : null}
      {shot ? <PreviewAsk agent={agent} shot={shot} onClose={() => setShot(null)} /> : null}
      <div ref={body} className="web-body relative min-h-0 flex-1">
        {!url ? (
          <p className="m-0 p-6 text-center text-ink-2">Type the address of the app this agent is running.</p>
        ) : api ? null : (
          <iframe key={reload} title="Preview" src={url} className="h-full w-full border-0 bg-white" />
        )}
      </div>
    </div>
  );
}
