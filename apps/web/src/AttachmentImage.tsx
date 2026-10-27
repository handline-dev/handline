// The one picture of an uploaded image: a small copy in the text, the original in the ImageViewer behind a click. Chat pictures, memos and task
// descriptions all draw it (the editor draws it for an image node too).
import { createContext, useContext, useState } from 'react';
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
  const small = useAttachmentUrl(id, true);
  const inThumbs = useContext(Thumbs);
  thumb ??= inThumbs;
  const [open, setOpen] = useState(false);
  const label = name || 'image';
  if (small.failed)
    return (
      <span className="attachment failed muted text-[12px]" role="note">
        The image {label} could not be loaded.
      </span>
    );
  if (small.gone)
    return (
      <span className="attachment gone muted text-[12px]" title="This image is no longer kept">
        {label}
      </span>
    );
  return (
    <>
      <button
        type="button"
        className={`attachment items-start border-0 bg-transparent p-0 text-left ${thumb ? 'mr-1.5 inline-flex h-11 w-16 align-top' : 'flex h-40 min-w-24 max-w-full'}`}
        title={label}
        aria-label={`Open ${label}`}
        onClick={() => setOpen(true)}
      >
        {small.url ? (
          <img src={small.url} alt={label} className={`rounded-chip object-cover ${thumb ? 'h-11 w-16' : 'max-h-40 max-w-full'}`} />
        ) : (
          <span className="muted text-[12px]">{label}</span>
        )}
      </button>
      <AnimatedList>
        {open ? <ImageViewer key="original" pictures={group ?? [{ id, name: label }]} start={at} onClose={() => setOpen(false)} /> : null}
      </AnimatedList>
    </>
  );
}
