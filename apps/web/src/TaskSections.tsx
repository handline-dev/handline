// The task page's two texts a person can write: the brief and the Result. Each shows its text, a pencil on hover or keyboard focus turns it into the
// rich editor (Rich | Markdown) with Save and Cancel; a task in any state can be edited. Clicking the text does not edit, so it stays selectable.
import { Pencil } from 'lucide-react';
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { TaskDetail as Detail } from '@handline/client';
import { BodyEditor } from '#src/BodyEditor.tsx';
import { KeepPhotos } from '#src/keepPhotos.ts';
import { Markdown } from '#src/Markdown.tsx';
import { RichText } from '#src/RichText.tsx';
import { useEditTask } from '#src/TaskInline.tsx';
import { Button } from '#src/ui/index.ts';

/** What the agent writes when it ends a task without a word. */
const BARE = /^\s*(done\.?)?\s*$/i;

const PENCIL =
  'absolute top-0 right-0 inline-flex size-7 cursor-pointer items-center justify-center rounded-sm border-0 bg-transparent text-ink-3 opacity-0 transition-opacity tint group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-acc group-focus-within:opacity-100 [@media(hover:none)]:opacity-100';

/** Text with a pencil at its top right; `editor` draws the field when the pencil has been pressed. */
function Editable({
  can,
  label,
  start,
  children,
  editor,
}: {
  can: boolean;
  label: string;
  /** Opens in the editor, as the one that adds a Result does. */
  start?: boolean;
  children: ReactNode;
  editor: (close: () => void) => ReactNode;
}) {
  const [on, setOn] = useState(!!start);
  if (on) return <>{editor(() => setOn(false))}</>;
  return (
    <div className="group relative min-w-0">
      {children}
      {can ? (
        <button type="button" className={PENCIL} aria-label={`Edit ${label}`} title={`Edit ${label}`} onClick={() => setOn(true)}>
          <Pencil width={14} height={14} strokeWidth={1.75} aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

/** The text in its editor: Rich | Markdown at the toolbar's end, "Saved as Markdown" in the foot with Cancel and Save, which waits for a running upload. */
function SectionEditor({
  t,
  label,
  value,
  field,
  onClose,
}: {
  t: Detail;
  label: string;
  value: string;
  field: 'body' | 'evidence';
  onClose: () => void;
}) {
  const edit = useEditTask(t);
  const [text, setText] = useState(value);
  const [busy, setBusy] = useState(false);
  const uploading = useRef(false); // a Save in the tick of a paste must already see the upload
  const save = () => (uploading.current ? undefined : text === value ? onClose() : edit.save({ [field]: text }, onClose));
  return (
    <div className="grid gap-2" onKeyDown={(e) => e.key === 'Escape' && (e.stopPropagation(), onClose())}>
      <div className="rounded-sm border border-line-strong bg-canvas focus-within:border-ink-3 [&_.body-editor]:border-0 [&_textarea]:border-0 [&_textarea]:bg-transparent [&_.body-editor]:bg-transparent [&_.body-field]:gap-0 [&>div>div:first-child]:border-b [&>div>div:first-child]:border-line [&>div>div:first-child]:p-1">
        <BodyEditor toolbar value={text} onChange={setText} onBusy={(b) => ((uploading.current = b), setBusy(b))} label={label} />
      </div>
      <div className="flex items-center gap-2">
        <span className="mr-auto text-xs text-ink-3">Saved as Markdown</span>
        {edit.error ? (
          <span className="text-bad" role="alert">
            {edit.error}
          </span>
        ) : null}
        <Button label="Cancel" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="solid" label="Save" disabled={busy || edit.pending} onClick={save}>
          {edit.pending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  );
}

/** The longest the brief stands before it is cut: twelve lines, or 60% of the window, whichever is less. */
const CLAMP = 'max-h-[min(12lh,60vh)] overflow-hidden';

/** The brief: up to 12 lines or 60% of the window's height; longer text fades out over its last line and ends in "Show more". Short text shows whole. */
export function Brief({ t, can }: { t: Detail; can: boolean }) {
  const [more, setMore] = useState(false);
  const [cut, setCut] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => !more && setCut(el.scrollHeight > el.clientHeight + 1);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [t.body, more]);
  return (
    <div className="tbody-text mt-3">
      <Editable
        can={can}
        label="Description"
        editor={(close) => <SectionEditor t={t} label="Description" value={t.body} field="body" onClose={close} />}
      >
        <KeepPhotos.Provider value>
          {t.body ? (
            <>
              <div
                ref={box}
                className={`${more ? '' : CLAMP} ${cut && !more ? '[mask-image:linear-gradient(to_bottom,#000_calc(100%-1lh),transparent)]' : ''}`}
              >
                <Markdown text={t.body} />
              </div>
              {cut || more ? (
                <button type="button" className="mt-1 border-0 bg-transparent p-0 text-acc-ink" onClick={() => setMore(!more)}>
                  {more ? 'Show less' : 'Show more'}
                </button>
              ) : null}
            </>
          ) : (
            <span className="muted">{can ? 'Add a description' : ''}</span>
          )}
        </KeepPhotos.Provider>
      </Editable>
    </div>
  );
}

/** What the agent wrote when it ended the task, with what it points at as links. Nothing when it is only "Done"; a quiet "Add a result" on hover when there is none. */
export function Result({ t, can }: { t: Detail; can: boolean }) {
  const [adding, setAdding] = useState(false);
  const text = t.evidence ?? '';
  const none = BARE.test(text);
  if (none && !adding)
    return can ? (
      <button
        type="button"
        className="muted mt-3 cursor-pointer self-start border-0 bg-transparent p-0 opacity-0 transition-opacity focus-visible:opacity-100 hover:opacity-100"
        onClick={() => setAdding(true)}
      >
        Add a result
      </button>
    ) : null;
  return (
    <section aria-label="Result" className="mt-4">
      <h3 className="m-0 mb-1 text-sm font-semibold">Result</h3>
      <Editable
        can={can}
        label="Result"
        start={adding}
        editor={(close) => <SectionEditor t={t} label="Result" value={text} field="evidence" onClose={() => (setAdding(false), close())} />}
      >
        <p className="tbody-text m-0 whitespace-pre-wrap">
          <RichText text={text} />
        </p>
      </Editable>
    </section>
  );
}
