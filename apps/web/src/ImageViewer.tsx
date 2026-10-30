// The one enlarged view of a picture: dimmed scrim, zoom (ViewerStage.tsx) and, under a PhotoNotes place, marking what to change.
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { initialMarks, keptMarks, marksReducer, marksToShapes } from '@handline/app-core';
import { sentence, classify, useAttachmentUrl } from '@handline/app-data';
import { marked } from '#src/Annotate.tsx';
import { notesText, useNotesPlace } from '#src/PhotoNotes.tsx';
import { ViewerStage } from '#src/ViewerStage.tsx';
import { Button, Icon } from '#src/ui/index.ts';

export type Picture = { id?: string; url?: string; name: string };

export function ImageViewer({ pictures, start = 0, onClose }: { pictures: Picture[]; start?: number; onClose: () => void }) {
  const [at, setAt] = useState(Math.min(start, pictures.length - 1));
  const pic = pictures[at];
  const place = useNotesPlace();
  const full = useAttachmentUrl(pic?.id ?? '', false);
  const url = pic?.url ?? full.url;
  const canMark = !!place && !!pic?.id && !!url;
  const [marking, setMarking] = useState(false);
  const [marks, dispatch] = useReducer(marksReducer, undefined, initialMarks);
  const [asking, setAsking] = useState<'close' | 'stop' | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState('');
  const image = useRef<HTMLImageElement>(null);
  const fit = useRef<(() => void) | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const kept = keptMarks(marks.marks);

  const stop = () => {
    setMarking(false);
    dispatch({ type: 'cancel' });
  };
  // Leaving with marks that are not sent asks first; with none it just leaves.
  const leave = (what: 'close' | 'stop') => {
    if (marking && marks.marks.length) return setAsking(what);
    if (what === 'close') onClose();
    else stop();
  };
  const step = (by: number) => !marking && setAt((i) => Math.min(pictures.length - 1, Math.max(0, i + by)));
  const live = useRef({ leave, step, canMark, marking });
  useEffect(() => void (live.current = { leave, step, canMark, marking }));
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    root.current?.focus();
    const key = (e: KeyboardEvent) => {
      const { leave, step, canMark, marking } = live.current;
      if (e.target instanceof Element && e.target.closest('input, textarea, select')) return;
      if (e.key === 'Escape') leave(marking ? 'stop' : 'close');
      else if (e.key === 'ArrowLeft') step(-1);
      else if (e.key === 'ArrowRight') step(1);
      else if ((e.key === 'm' || e.key === 'M') && canMark && !e.metaKey && !e.ctrlKey && !e.altKey) setMarking(true);
    };
    addEventListener('keydown', key);
    return () => {
      removeEventListener('keydown', key);
      previous?.focus?.();
    };
  }, []);

  const send = async () => {
    if (!place || !pic || !image.current || !kept.length || sending) return;
    setSending(true);
    setProblem('');
    try {
      const drawn = await marked(image.current, marksToShapes(marks.marks, Math.max(3, Math.round(image.current.naturalWidth / 250))));
      await place.send(
        notesText(
          pic.name,
          kept.map((m) => m.text.trim().replace(/\s*\n\s*/g, ' ')),
        ),
        drawn,
        pic.name,
      );
      onClose();
    } catch (e) {
      setProblem(sentence(classify(e)));
      setSending(false);
    }
  };

  const onZoomed = useCallback((z: boolean) => setZoomed(z), []);
  if (!pic) return null;
  return (
    <div
      ref={root}
      role="dialog"
      aria-modal="true"
      aria-label={pic.name}
      tabIndex={-1}
      className="image-viewer fixed inset-0 z-50 flex flex-col bg-black/95 text-white outline-none"
    >
      <div className="flex h-12 flex-none items-center gap-3 px-4 text-sm">
        <span className="min-w-0 truncate font-medium">{pic.name}</span>
        {pictures.length > 1 ? (
          <span className="opacity-70">
            {at + 1} of {pictures.length}
          </span>
        ) : null}
        <span className="flex-1" />
        {zoomed ? (
          <button type="button" className="rounded-full border-0 bg-white/20 px-3 py-1 text-white" onClick={() => fit.current?.()}>
            Fit
          </button>
        ) : null}
        {url && !marking ? <PhotoMenu url={url} name={pic.name} /> : null}
        <button type="button" className="inline-flex items-center gap-1 border-0 bg-transparent text-white" onClick={() => leave('close')}>
          <Icon name="close" size={14} aria-hidden />
          Close
        </button>
      </div>
      <div className="relative flex min-h-0 flex-1 px-4 sm:px-14">
        {url ? (
          <ViewerStage
            key={at}
            url={url}
            name={pic.name}
            marking={marking}
            marks={marks}
            dispatch={dispatch}
            image={image}
            onZoomed={onZoomed}
            fitRef={fit}
            onScrim={() => leave('close')}
          />
        ) : (
          <p className="m-auto opacity-70" onClick={() => leave('close')}>
            {full.failed ? 'The image could not be loaded.' : full.gone ? 'This image is no longer kept.' : 'Loading…'}
          </p>
        )}
        {!marking && at > 0 ? <Arrow side="left" label="Previous picture" onClick={() => step(-1)} /> : null}
        {!marking && at < pictures.length - 1 ? <Arrow side="right" label="Next picture" onClick={() => step(1)} /> : null}
      </div>
      <div className="flex min-h-16 flex-none flex-col items-center justify-center gap-1 px-4 py-2">
        {problem ? (
          <p className="m-0 text-sm text-bad" role="alert">
            {problem}
          </p>
        ) : null}
        {asking ? (
          <div className="flex flex-col items-center gap-2" role="alertdialog" aria-label={`Discard ${marks.marks.length} notes?`}>
            <span>
              Discard {marks.marks.length} {marks.marks.length === 1 ? 'note' : 'notes'}? They are not sent yet.
            </span>
            <span className="flex gap-2">
              <Button
                label="Discard"
                className="!text-bad"
                onClick={() => {
                  dispatch({ type: 'clear' });
                  if (asking === 'close') onClose();
                  else {
                    setMarking(false);
                    setAsking(null);
                  }
                }}
              />
              <Button variant="solid" label="Keep marking" onClick={() => setAsking(null)} />
            </span>
          </div>
        ) : marking ? (
          <div className="flex items-center gap-4">
            {kept.length ? (
              <Button variant="solid" size="lg" label={place!.button(kept.length)} busy={sending} onClick={() => void send()}>
                {place!.button(kept.length)}
              </Button>
            ) : (
              <span className="opacity-80">Drag on the photo to mark</span>
            )}
            <button type="button" className="border-0 bg-transparent text-sm text-white underline" onClick={() => leave('stop')}>
              Cancel
            </button>
          </div>
        ) : canMark ? (
          <Button
            variant="outline"
            label="Mark what to change"
            icon="edit"
            keys="M"
            className="!bg-white/20 !text-white"
            onClick={() => setMarking(true)}
          >
            Mark what to change
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** More: copy the photo to the clipboard, or save it as a file. */
function PhotoMenu({ url, name }: { url: string; name: string }) {
  const [open, setOpen] = useState(false);
  const item = 'block w-full cursor-pointer border-0 bg-transparent px-3 py-1.5 text-left text-sm text-ink';
  const copy = async () => {
    setOpen(false);
    const blob = await (await fetch(url)).blob();
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
  };
  return (
    <span className="relative">
      <button
        type="button"
        aria-label="More"
        aria-haspopup="menu"
        aria-expanded={open}
        className="border-0 bg-transparent px-2 text-lg leading-none text-white"
        onClick={() => setOpen(!open)}
      >
        ⋯
      </button>
      {open ? (
        <div role="menu" className="absolute right-0 z-20 mt-1 w-40 rounded-md bg-panel py-1 shadow-[var(--shadow)]">
          <button type="button" role="menuitem" className={item} onClick={() => void copy().catch(() => undefined)}>
            Copy photo
          </button>
          <a role="menuitem" className={`${item} no-underline`} href={url} download={name} onClick={() => setOpen(false)}>
            Save photo
          </a>
        </div>
      ) : null}
    </span>
  );
}

function Arrow({ side, label, onClick }: { side: 'left' | 'right'; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`absolute top-1/2 ${side === 'left' ? 'left-2' : 'right-2'} grid size-10 -translate-y-1/2 place-items-center rounded-full border-0 bg-white/20 text-lg text-white`}
      onClick={onClick}
    >
      {side === 'left' ? '‹' : '›'}
    </button>
  );
}
