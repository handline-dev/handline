// A task's text with its images (docs/story1-task-images-graph.md section A): the daemon's `parts`, or the same cut made here.
import type { Attachment } from '@handline/client';
import { Pictures } from './ChatAttach.tsx';
import { cutText, type TextPart } from '@handline/app-core';
import { RichText } from './RichText.tsx';

export function TextParts({ text, parts }: { text: string; parts?: TextPart[] | undefined }) {
  const cut = parts ?? cutText(text);
  if (!cut) return <RichText text={text} />;
  return (
    <>
      {cut.map((p, i) =>
        'text' in p ? (
          <RichText key={i} text={p.text} />
        ) : p.gone ? (
          <em key={i} className="muted">
            {p.caption || 'image'} (no longer kept)
          </em>
        ) : (
          <Pictures key={i} items={[p.image as Attachment]} />
        ),
      )}
    </>
  );
}
