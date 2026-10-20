// The task description editor: Markdown stored, shown and edited rich (Tiptap), with a source view for the raw text. A pasted or dropped image is
// uploaded and goes in at the cursor as `![name](attachment:ID)`; an image node draws with the same AttachmentImage as chat and memos.
import { useEffect, useRef, useState } from 'react';
import { mergeAttributes, type Editor } from '@tiptap/core';
import { EditorContent, NodeViewWrapper, ReactNodeViewRenderer, useEditor, type NodeViewProps } from '@tiptap/react';
import { NodeSelection } from '@tiptap/pm/state';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import Image from '@tiptap/extension-image';
import type { Attachment } from '@handline/client';
import { imageRef, insertImageRef } from '@handline/app-core';
import { classify, sentence, useUploadImage, whyNotImage } from '@handline/app-data';
import { AttachmentImage } from '#src/AttachmentImage.tsx';
import { forEditor, keepTyped } from '#src/bodyMarkdown.ts';

const idOf = (src: unknown) => /^attachment:(.+)$/.exec(String(src ?? ''))?.[1];

function ImageView({ node }: NodeViewProps) {
  const id = idOf(node.attrs.src);
  return (
    <NodeViewWrapper contentEditable={false}>
      {id ? <AttachmentImage id={id} name={String(node.attrs.alt ?? '')} /> : <img src={String(node.attrs.src ?? '')} alt={String(node.attrs.alt ?? '')} />}
    </NodeViewWrapper>
  );
}

export const BodyImage = Image.extend({
  // `attachment:` is no URL the browser can load: the node view draws it, and the HTML copy carries the reference as text.
  renderHTML: ({ HTMLAttributes }) => ['img', mergeAttributes(HTMLAttributes)],
  addNodeView: () => ReactNodeViewRenderer(ImageView),
});

const BOX = 'rounded-ctl border border-line bg-panel px-2 py-1.5 text-ink';

const imagesOf = (list: FileList | null | undefined): File[] => Array.from(list ?? []).filter((f) => f.type.startsWith('image/'));

/** The body field. `onBusy` tells the form an upload is running (Save waits). */
export function BodyEditor(props: { value: string; onChange: (md: string) => void; onBusy?: (busy: boolean) => void; label?: string; id?: string }) {
  const { onBusy } = props;
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
  const takeRef = useRef(take);
  takeRef.current = take;

  const editor = useEditor({
    extensions: [StarterKit, BodyImage, Markdown],
    content: forEditor(props.value),
    contentType: 'markdown',
    editorProps: {
      attributes: { class: `${BOX} body-editor min-h-24 focus-within:border-ink-3 outline-none`, ...(props.id ? { id: props.id } : {}), role: 'textbox', 'aria-multiline': 'true' },
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
    },
  });
  ed.current = editor;
  useEffect(() => keepTyped(editor), [editor]);

  const toSource = (on: boolean) => {
    if (!on && editor) editor.commands.setContent(forEditor(last.current), { contentType: 'markdown' });
    setSource(on);
  };

  return (
    <div className="body-field grid min-w-40 flex-1 gap-1">
      <div className="flex justify-end">
        <button type="button" className="muted border-0 bg-transparent p-0 text-[12px] underline cursor-pointer" aria-pressed={source} onClick={() => toSource(!source)}>
          {source ? 'Rich view' : 'Source'}
        </button>
      </div>
      {source ? (
        <textarea
          className={`${BOX} font-mono`}
          rows={6}
          aria-label={props.label ?? 'Body (Markdown)'}
          value={props.value}
          onChange={(e) => {
            last.current = e.target.value;
            change.current(e.target.value);
          }}
          onPaste={(e) => {
            const files = imagesOf(e.clipboardData.files);
            if (!files.length) return;
            e.preventDefault();
            void take(files);
          }}
        />
      ) : (
        <EditorContent editor={editor} />
      )}
      {problem ? <span className="muted">{problem}</span> : busy ? <span className="muted">Uploading image…</span> : null}
    </div>
  );
}
