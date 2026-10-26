// The one enlarged view of a picture: nearly the whole window, Esc or a click outside closes it, and arrows (or ←/→) step through several.
// Every place that shows an attached image opens this; a picture is an uploaded id or, for a draft not yet sent, its local preview URL.
import { useEffect, useState } from 'react';
import { useAttachmentUrl } from '@handline/app-data';
import { Dialog } from '#src/ui/index.ts';

export type Picture = { id?: string; url?: string; name: string };

export function ImageViewer({ pictures, start = 0, onClose }: { pictures: Picture[]; start?: number; onClose: () => void }) {
  const [at, setAt] = useState(Math.min(start, pictures.length - 1));
  const step = (by: number) => setAt((i) => Math.min(pictures.length - 1, Math.max(0, i + by)));
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') step(-1);
      else if (e.key === 'ArrowRight') step(1);
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  });
  const pic = pictures[at];
  if (!pic) return null;
  return (
    <Dialog title={pic.name} onClose={onClose} full>
      <div
        className="image-viewer relative flex h-full items-center justify-center"
        onClick={(e) => e.target === e.currentTarget && onClose()}
      >
        <Shown key={at} pic={pic} />
        {pictures.length > 1 ? (
          <>
            {at > 0 ? <Arrow side="left" label="Previous picture" onClick={() => step(-1)} /> : null}
            {at < pictures.length - 1 ? <Arrow side="right" label="Next picture" onClick={() => step(1)} /> : null}
            <span className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-chip bg-panel px-2 text-[12px] muted">
              {at + 1} / {pictures.length}
            </span>
          </>
        ) : null}
      </div>
    </Dialog>
  );
}

function Arrow({ side, label, onClick }: { side: 'left' | 'right'; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`absolute top-1/2 ${side === 'left' ? 'left-2' : 'right-2'} -translate-y-1/2 rounded-chip bg-panel px-3 py-2 text-title`}
      onClick={onClick}
    >
      {side === 'left' ? '‹' : '›'}
    </button>
  );
}

function Shown({ pic }: { pic: Picture }) {
  const full = useAttachmentUrl(pic.id ?? '', false);
  const url = pic.url ?? full.url;
  if (url) return <img src={url} alt={pic.name} className="max-h-full max-w-full object-contain" />;
  if (full.failed) return <p className="muted">The image could not be loaded.</p>;
  return <p className="muted">{full.gone ? 'This image is no longer kept.' : 'Loading…'}</p>;
}
