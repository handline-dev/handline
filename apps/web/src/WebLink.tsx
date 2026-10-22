// A web address in a message: a click asks where to open it, in the app (a preview pane beside the chat when there is room) or in the browser.
import { useState } from 'react';
import { fillTab, openTab, splitPane, tabId } from '@handline/app-core';
import { Button, Dialog } from '#src/ui/index.ts';
import { useWorkspaceIfAny } from '#src/Workspace.tsx';

const LINK = 'ref border-0 bg-transparent p-0 font-mono text-[12px] leading-4 text-link underline cursor-pointer break-all text-left';

/** The web addresses of a text, trailing punctuation left out. */
export function findUrls(text: string): { start: number; end: number }[] {
  return [...text.matchAll(/https?:\/\/[^\s<>"'`]+/g)].map((m) => {
    const url = m[0].replace(/[.,;:!?)\]}]+$/, '');
    return { start: m.index!, end: m.index! + url.length };
  });
}

export function WebLink({ url }: { url: string }) {
  const ws = useWorkspaceIfAny();
  const [asking, setAsking] = useState(false);
  const outside = () => (window.open(url, '_blank', 'noopener,noreferrer'), setAsking(false));
  const inside = () => {
    if (!ws) return outside();
    const params = { id: 'new', url };
    ws.apply((w) => {
      const split = ws.canSplit(w.active) ? splitPane(w, w.active) : null;
      return split ? fillTab(split, tabId(w, 'new', {}), 'web', params) : openTab(w, 'web', params, w.active);
    });
    setAsking(false);
  };
  return (
    <>
      <button type="button" className={LINK} data-url={url} title="Open this link" onClick={() => setAsking(true)}>
        {url}
      </button>
      {asking ? (
        <Dialog title="Open link" onClose={() => setAsking(false)} action={{ label: 'In app', onClick: inside }}>
          <p className="m-0 break-all text-body text-ink-2">{url}</p>
          <div>
            <Button variant="outline" label="External browser" onClick={outside} />
          </div>
        </Dialog>
      ) : null}
    </>
  );
}
