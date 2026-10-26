// The one picture of an uploaded image: a small copy in the text, the original in the ImageViewer behind a click. Chat pictures, memos and task
// descriptions all draw it (the editor draws it for an image node too).
import { useState } from 'react';
import { useAttachmentUrl } from '@handline/app-data';
import { ImageViewer, type Picture } from '#src/ImageViewer.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';

export function AttachmentImage({ id, name, group, at = 0 }: { id: string; name: string; group?: Picture[]; at?: number }) {
  const small = useAttachmentUrl(id, true);
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
        className="attachment flex h-40 min-w-24 max-w-full items-start border-0 bg-transparent p-0 text-left"
        title={label}
        aria-label={`Open ${label}`}
        onClick={() => setOpen(true)}
      >
        {small.url ? (
          <img src={small.url} alt={label} className="max-h-40 max-w-full rounded-chip object-cover" />
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
