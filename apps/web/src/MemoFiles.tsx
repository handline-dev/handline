// The files a task memo carries: images as small pictures that open full size on a click, Markdown rendered (read-only, as the description is).
import { useEffect } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import type { MemoFile } from '@handline/client';
import { useAttachmentBytes } from '@handline/app-data';
import { AttachmentImage } from './AttachmentImage.tsx';
import { BodyImage } from './BodyEditor.tsx';
import { forEditor, resolveImages } from './bodyMarkdown.ts';

export function MemoFiles({ files }: { files: MemoFile[] }) {
  return (
    <div className="memo-files grid gap-2">
      {files.map((f) =>
        f.kind === 'image' ? (
          <AttachmentImage key={f.id} id={f.id} name={f.name} />
        ) : (
          <MarkdownFile key={f.id} id={f.id} name={f.name} pictures={files.filter((x) => x.kind === 'image')} />
        ),
      )}
    </div>
  );
}

function MarkdownFile({ id, name, pictures }: { id: string; name: string; pictures: MemoFile[] }) {
  const read = useAttachmentBytes(id, false);
  const md = read.state === 'ready' ? resolveImages(new TextDecoder().decode(read.bytes), pictures) : '';
  const editor = useEditor({ extensions: [StarterKit, BodyImage, Markdown], content: '', contentType: 'markdown', editable: false });
  useEffect(() => {
    if (editor && read.state === 'ready') editor.commands.setContent(forEditor(md), { contentType: 'markdown' });
  }, [editor, read.state, md]);
  return (
    <section className="rounded-ctl border border-line bg-panel px-2 py-1.5" aria-label={name}>
      <h4 className="m-0 mb-1 font-mono text-[12px] font-normal text-ink-3">{name}</h4>
      {read.state === 'gone' || read.state === 'failed' ? (
        <p className="muted m-0">{read.state === 'gone' ? 'This file is no longer kept.' : 'Could not read this file.'}</p>
      ) : (
        <EditorContent
          editor={editor}
          className="body-editor max-h-96 overflow-auto [&_h1]:text-[18px] [&_h1]:leading-6 [&_h2]:text-[15px] [&_h2]:leading-5 [&_h3]:text-[13px]"
        />
      )}
    </section>
  );
}
