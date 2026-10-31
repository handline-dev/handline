// The one picture of an uploaded image: a small copy in the text, the original in the ImageViewer behind a click. Chat pictures, memos and task
// descriptions all draw it (the editor draws it for an image node too).
import { ImageOff, LoaderCircle, RotateCw } from 'lucide-react';
import { createContext, useContext, useEffect, useState } from 'react';
import { useAttachmentUrl } from '@handline/app-data';
import { ImageViewer, type Picture } from '#src/ImageViewer.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';

/** Pictures drawn under it are thumbnails: a comment's text draws its inline images through RichText, which cannot pass `thumb` down. */
export const Thumbs = createContext(false);

/** `thumb` is a fixed small thumbnail (a comment's pictures), never the full-height copy a chat message shows. */
export function AttachmentImage({
  id,
  name,
  group,
  at = 0,
  thumb,
}: {
  id: string;
  name: string;
  group?: Picture[];
  at?: number;
  thumb?: boolean;
}) {
  const [attempt, setAttempt] = useState(0);
  const small = useAttachmentUrl(id, true, attempt);
  const inThumbs = useContext(Thumbs);
  thumb ??= inThumbs;
  const [open, setOpen] = useState(false);
  const [drawn, setDrawn] = useState(false); // the picture has its pixels: until then its box keeps the room it will fill
  const label = name || 'image';
  if (small.failed)
    return (
      <span className={`attachment failed inline-flex flex-col align-top ${thumb ? 'mr-1.5 w-20' : ''}`} role="note">
        <span className="flex h-14 w-20 flex-col items-center justify-center gap-0.5 rounded-sm border border-line bg-surface text-xs text-bad">
          <ImageOff width={16} height={16} strokeWidth={1.75} aria-hidden />
          Couldn't load
          <button
            type="button"
            className="inline-flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-xs text-acc-ink"
            onClick={() => setAttempt(attempt + 1)}
          >
            <RotateCw width={11} height={11} strokeWidth={1.75} aria-hidden />
            Retry
          </button>
        </span>
        <span className="truncate text-xs text-ink-3">{label}</span>
      </span>
    );
  if (small.gone)
    return (
      <span className="attachment gone muted text-[12px]" title={small.removed ? 'Photo deleted' : 'This image is no longer kept'}>
        {small.removed ? 'Photo deleted' : label}
      </span>
    );
  return (
    <>
      <button
        type="button"
        className={`attachment items-start border-0 bg-transparent p-0 text-left ${thumb ? 'mr-1.5 inline-flex w-20 flex-col gap-0.5 align-top' : 'flex min-w-24 max-w-full'} ${!thumb && !drawn ? 'h-40' : ''}`}
        title={label}
        aria-label={`Open ${label}`}
        onClick={() => setOpen(true)}
      >
        {small.url ? (
          <img
            src={small.url}
            alt={label}
            onLoad={() => setDrawn(true)}
            onError={() => setDrawn(true)}
            className={`rounded-sm object-cover ${thumb ? 'h-14 w-20' : 'max-h-40 max-w-full'}`}
          />
        ) : thumb ? (
          <Loading />
        ) : (
          <span className="muted text-[12px]">{label}</span>
        )}
        {thumb ? <span className="w-full truncate text-xs text-ink-3">{label}</span> : null}
      </button>
      <AnimatedList>
        {open ? <ImageViewer key="original" pictures={group ?? [{ id, name: label }]} start={at} onClose={() => setOpen(false)} /> : null}
      </AnimatedList>
    </>
  );
}

/** The thumbnail's box at its final size while the picture is read: a soft shimmer, and a small spinner once it has taken 300 ms. */
function Loading() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 300);
    return () => clearTimeout(t);
  }, []);
  return (
    <span
      className="flex h-14 w-20 items-center justify-center rounded-sm bg-raised animate-pulse"
      role="img"
      aria-label="Loading the picture"
    >
      {slow ? <LoaderCircle width={16} height={16} strokeWidth={1.75} className="animate-spin text-ink-3" aria-hidden /> : null}
    </span>
  );
}
