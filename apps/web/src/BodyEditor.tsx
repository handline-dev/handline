// The task description editor: Markdown stored, shown and edited rich (Tiptap); `toolbar` adds the formatting buttons and the Rich | Markdown switch. A pasted or dropped image is
// uploaded and goes in at the cursor as `![name](attachment:ID)`; an image node draws with the same AttachmentImage as chat and memos.
import { useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { mergeAttributes, type Editor } from '@tiptap/core';
import { EditorContent, NodeViewWrapper, ReactNodeViewRenderer, useEditor, type NodeViewProps } from '@tiptap/react';
import { NodeSelection } from '@tiptap/pm/state';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import Image from '@tiptap/extension-image';
import { Bold, Code, Italic, Link, List } from 'lucide-react';
import type { Attachment } from '@handline/client';
import { imageRef, insertImageRef, mentionAt, mentionNames } from '@handline/app-core';
import { classify, sentence, useUploadImage, whyNotImage } from '@handline/app-data';
import { AttachmentImage, Thumbs } from '#src/AttachmentImage.tsx';
import { forEditor, keepTyped } from '#src/bodyMarkdown.ts';
import { Segmented } from '#src/ui/Segmented.tsx';

const idOf = (src: unknown) => /^attachment:(.+)$/.exec(String(src ?? ''))?.[1];

function ImageView({ node }: NodeViewProps) {
  const id = idOf(node.attrs.src);
  const thumbs = useContext(Thumbs); // a compact editor draws its pictures as the small thumbnails of the view, side by side
  return (
    <NodeViewWrapper contentEditable={false} className={thumbs ? 'inline-block align-top' : undefined}>
      {id ? (
        <AttachmentImage id={id} name={String(node.attrs.alt ?? '')} />
      ) : (
        <img src={String(node.attrs.src ?? '')} alt={String(node.attrs.alt ?? '')} />
      )}
    </NodeViewWrapper>
  );
}

export const BodyImage = Image.extend({
  // `attachment:` is no URL the browser can load: the node view draws it, and the HTML copy carries the reference as text.
  renderHTML: ({ HTMLAttributes }) => ['img', mergeAttributes(HTMLAttributes)],
  addNodeView: () => ReactNodeViewRenderer(ImageView),
});

/** Compact: one or two lines. The picture nodes, and the empty paragraph after them, sit inline so the thumbnails make one row. */
const COMPACT =
  'min-h-9 [&_p]:m-0 [&>div]:mr-1.5 [&>div]:inline-block [&>div]:align-top [&>p:has(>br:only-child)]:inline-block [&>p:has(>br:only-child)]:min-w-4';
const BOX = 'rounded-sm border border-line-strong bg-canvas px-2 py-1.5 text-ink';

const imagesOf = (list: FileList | null | undefined): File[] => Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));

/** The body field. `onBusy` tells the form an upload is running (Save waits). */
export function BodyEditor(props: {
  value: string;
  onChange: (md: string) => void;
  onBusy?: (busy: boolean) => void;
  label?: string;
  id?: string;
  /** ⌘↵ / Ctrl+↵ in the field. */
  onSubmit?: () => void;
  /** The field is only the text (a comment edited in place). */
  plain?: boolean;
  /** A toolbar of formatting buttons and a Rich | Markdown switch at its right end: for the brief and the Result, not for a comment or a chat. */
  toolbar?: boolean;
  /** One or two lines at rest, small padding, growing with the text (comments); the default stays a few lines tall. */
  compact?: boolean;
  /** Buttons that stand in the field's foot: a field that is one box with its controls. */
  controls?: ReactNode;
  /** Typing `@` offers these agents, the ones near the task first. */
  mentions?: { names: readonly string[]; near: readonly string[]; state?: (name: string) => string };
}) {
  const { onBusy } = props;
  const [pick, setPick] = useState<{ names: string[]; len: number; at: number } | null>(null);
  const pickRef = useRef(pick);
  pickRef.current = pick;
  const live = useRef(props);
  live.current = props;
  const upload = useUploadImage();
  const uploadRef = useRef(upload);
  uploadRef.current = upload;
  const [source, setSource] = useState(false);
  const inSource = useRef(false);
  inSource.current = source;
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(0);
  const running = useRef(0);
  const change = useRef(props.onChange);
  change.current = props.onChange;
  const last = useRef(props.value);
  const ed = useRef<Editor | null>(null);

  const take = async (files: File[]) => {
    setProblem('');
    // The form hears at once, not after a render: a Save in the same tick must already see the upload.
    const run = (d: number) => {
      running.current += d;
      setBusy(running.current);
      onBusy?.(running.current > 0);
    };
    run(1);
    try {
      await put(files);
    } finally {
      run(-1);
    }
  };
  const put = async (files: File[]) => {
    // Uploads run side by side; the images go in together, in file order, at the cursor when the last one ends.
    const done = await Promise.allSettled(
      files.map(async (f) => {
        const why = whyNotImage(f);
        if (why) throw new Error(why);
        const name = f.name || 'image';
        const a = await uploadRef.current(f, name, f.type as Attachment['mime']);
        return { id: a.id, alt: name.replace(/\.[^.]*$/, '') };
      }),
    );
    const bad = done.find((r) => r.status === 'rejected');
    if (bad) setProblem(sentence(classify(bad.reason)));
    const got = done.flatMap((r) => (r.status === 'fulfilled' ? r.value : []));
    if (got.length) {
      if (ed.current && !inSource.current) {
        const e = ed.current;
        const nodes = got.map((g) => ({ type: 'image', attrs: { src: `attachment:${g.id}`, alt: g.alt } }));
        // A selected picture is kept: the new ones go after it.
        const at = e.state.selection instanceof NodeSelection ? e.state.selection.to : undefined;
        (at === undefined ? e.chain().focus().insertContent(nodes) : e.chain().focus().insertContentAt(at, nodes)).run();
      } else {
        last.current = insertImageRef(last.current, null, got.map((g) => imageRef(g.id, g.alt)).join('\n'));
        change.current(last.current);
      }
    }
  };
  const choose = (name: string) => {
    const p = pickRef.current;
    const e = ed.current;
    if (!p || !e) return;
    const to = e.state.selection.from;
    e.chain()
      .focus()
      .deleteRange({ from: to - p.len, to })
      .insertContent(`@${name} `)
      .run();
    setPick(null);
  };
  const sync = (e: Editor) => {
    const { $from, empty } = e.state.selection;
    const m = empty && live.current.mentions ? mentionAt($from.parent.textBetween(0, $from.parentOffset, '\n', '\ufffc')) : null;
    const names = m ? mentionNames(m.query, live.current.mentions!.names, live.current.mentions!.near) : [];
    setPick(m && names.length ? { names, len: m.length, at: 0 } : null);
  };
  const takeRef = useRef(take);
  takeRef.current = take;

  const editor = useEditor({
    extensions: [StarterKit, BodyImage, Markdown],
    content: forEditor(props.value),
    contentType: 'markdown',
    editorProps: {
      attributes: {
        class: `${BOX} body-editor ${props.compact ? COMPACT : 'min-h-24'} focus-within:border-ink-3 outline-none`,
        ...(props.id ? { id: props.id } : {}),
        role: 'textbox',
        'aria-multiline': 'true',
      },
      handleKeyDown: (_v, e) => {
        const p = pickRef.current;
        if (p && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
          setPick({ ...p, at: (p.at + (e.key === 'ArrowDown' ? 1 : p.names.length - 1)) % p.names.length });
          return true;
        }
        if (p && (e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
          choose(p.names[p.at]!);
          return true;
        }
        if (p && e.key === 'Escape') return (setPick(null), true);
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && live.current.onSubmit) return (live.current.onSubmit(), true);
        return false;
      },
      handlePaste: (_v, e) => {
        const files = imagesOf(e.clipboardData?.files);
        if (!files.length) return false;
        e.preventDefault();
        void takeRef.current(files);
        return true;
      },
      handleDrop: (_v, e) => {
        const files = imagesOf((e as DragEvent).dataTransfer?.files);
        if (!files.length) return false;
        e.preventDefault();
        void takeRef.current(files);
        return true;
      },
    },
    onUpdate: ({ editor: e }) => {
      last.current = e.getMarkdown();
      change.current(last.current);
      sync(e);
    },
    onSelectionUpdate: ({ editor: e }) => sync(e),
  });
  ed.current = editor;
  useEffect(() => keepTyped(editor), [editor]);

  const toSource = (on: boolean) => {
    if (!on && editor) editor.commands.setContent(forEditor(last.current), { contentType: 'markdown' });
    setSource(on);
  };

  return (
    <div className="body-field grid min-w-40 flex-1 gap-1">
      {props.toolbar ? <Toolbar editor={editor} source={source} onSource={toSource} /> : null}
      {source ? (
        <textarea
          className={`${BOX} font-mono`}
          rows={props.compact ? 2 : 8}
          aria-label={props.label ?? 'Body (Markdown)'}
          value={props.value}
          onChange={(e) => {
            last.current = e.target.value;
            change.current(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey) || !props.onSubmit) return;
            e.preventDefault();
            props.onSubmit();
          }}
          onPaste={(e) => {
            const files = imagesOf(e.clipboardData.files);
            if (!files.length) return;
            e.preventDefault();
            void take(files);
          }}
        />
      ) : (
        <Thumbs.Provider value={!!props.compact}>
          <EditorContent editor={editor} />
        </Thumbs.Provider>
      )}
      {pick ? (
        <ul
          className="mention-list m-0 grid list-none gap-0 rounded-sm border border-line bg-panel p-0.5"
          role="listbox"
          aria-label="Agents"
        >
          {pick.names.map((n, i) => (
            <li key={n} role="option" aria-selected={i === pick.at}>
              <button
                type="button"
                className={`w-full cursor-pointer border-0 px-2 py-1 text-left text-md text-ink ${i === pick.at ? 'bg-raised' : 'bg-transparent'}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(n)}
              >
                @{n}
                {live.current.mentions?.state ? <span className="ml-2 text-xs text-ink-3">{live.current.mentions.state(n)}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {props.controls ? (
        <div className="flex items-center justify-between gap-2 px-1.5 pb-1.5">
          <span />
          <span className="flex items-center gap-2">{props.controls}</span>
        </div>
      ) : null}
      {problem ? <span className="muted">{problem}</span> : busy ? <span className="muted">Uploading image…</span> : null}
    </div>
  );
}

const TOOL = 'inline-flex size-7 cursor-pointer items-center justify-center rounded-sm border-0 bg-transparent text-ink-2 tint';

/** The formatting buttons of the Rich view, and the switch between it and the Markdown text; the switch keeps the text. */
function Toolbar({ editor, source, onSource }: { editor: Editor | null; source: boolean; onSource: (on: boolean) => void }) {
  const run = (f: (c: ReturnType<Editor['chain']>) => ReturnType<Editor['chain']>) => editor && f(editor.chain().focus()).run();
  const link = () => {
    if (!editor) return;
    const href = window.prompt('Link address', editor.getAttributes('link').href ?? 'https://');
    if (href === null) return;
    if (!href.trim()) editor.chain().focus().unsetLink().run();
    else editor.chain().focus().extendMarkRange('link').setLink({ href: href.trim() }).run();
  };
  const tool = (label: string, icon: ReactNode, onClick: () => void) => (
    <button
      key={label}
      type="button"
      className={TOOL}
      aria-label={label}
      title={label}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
    >
      {icon}
    </button>
  );
  const ic = { width: 14, height: 14, strokeWidth: 1.75, 'aria-hidden': true } as const;
  return (
    <div className="flex items-center gap-0.5" role="toolbar" aria-label="Formatting">
      {source ? null : (
        <>
          {tool('Bold', <Bold {...ic} />, () => run((c) => c.toggleBold()))}
          {tool('Italic', <Italic {...ic} />, () => run((c) => c.toggleItalic()))}
          {tool('Link', <Link {...ic} />, link)}
          {tool('List', <List {...ic} />, () => run((c) => c.toggleBulletList()))}
          {tool('Code', <Code {...ic} />, () => run((c) => c.toggleCode()))}
        </>
      )}
      <span className="ml-auto">
        <Segmented
          label="Editor"
          value={source ? 'markdown' : 'rich'}
          onChange={(v) => onSource(v === 'markdown')}
          options={[
            { value: 'rich', label: 'Rich' },
            { value: 'markdown', label: 'Markdown' },
          ]}
        />
      </span>
    </div>
  );
}
