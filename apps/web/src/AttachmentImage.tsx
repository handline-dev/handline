// The one picture of an uploaded image: a small copy in the text, the original in a dialog behind a click. Chat pictures, memos and task
// descriptions all draw it (the editor draws it for an image node too).
import { useState } from 'react';
import { useAttachmentUrl } from '@handline/app-data';
import { Dialog } from '#src/ui/index.ts';
import { AnimatedList } from '#src/AnimatedList.tsx';

export function AttachmentImage({ id, name }: { id: string; name: string }) {
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
        className="attachment border-0 bg-transparent p-0"
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
      <AnimatedList>{open ? <Original key="original" id={id} name={label} onClose={() => setOpen(false)} /> : null}</AnimatedList>
    </>
  );
}

function Original({ id, name, onClose }: { id: string; name: string; onClose: () => void }) {
  const full = useAttachmentUrl(id, false);
  return (
    <Dialog title={name} onClose={onClose} action={{ label: 'Close', keys: 'Esc', onClick: onClose }}>
      {full.failed ? (
        <p className="muted">The image could not be loaded.</p>
      ) : full.url ? (
        <img src={full.url} alt={name} className="max-h-[70vh] max-w-full" />
      ) : (
        <p className="muted">{full.gone ? 'This image is no longer kept.' : 'Loading…'}</p>
      )}
    </Dialog>
  );
}
