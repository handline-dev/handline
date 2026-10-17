// Markdown for chat messages: headings, bullet and numbered lists, **bold**, `code` and fenced code. No HTML is read: every piece is a
// React text node or element. The text between the marks goes through RichText, so paths and T<N> and #N stay links.
import type { ReactNode } from 'react';
import { RichText } from './RichText.tsx';

type Block =
  | { kind: 'code'; text: string }
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'para'; text: string };

const FENCE = /^\s*```/;
const HEADING = /^(#{1,4})\s+(?!\d)(.+)$/;
const BULLET = /^\s*[-*]\s+(.*)$/;
const NUMBER = /^\s*\d+[.)]\s+(.*)$/;

export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.split('\n');
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: 'para', text: para.join('\n') });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (FENCE.test(line)) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !FENCE.test(lines[i]!); i++) code.push(lines[i]!);
      blocks.push({ kind: 'code', text: code.join('\n') });
      continue;
    }
    const heading = HEADING.exec(line);
    const item = BULLET.exec(line) ?? NUMBER.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: 'heading', level: heading[1]!.length, text: heading[2]! });
    } else if (item) {
      flush();
      const ordered = !BULLET.test(line);
      const last = blocks[blocks.length - 1];
      if (last?.kind === 'list' && last.ordered === ordered) last.items.push(item[1]!);
      else blocks.push({ kind: 'list', ordered, items: [item[1]!] });
    } else if (!line.trim()) flush();
    else para.push(line);
  }
  flush();
  return blocks;
}

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/;
const CODE = 'rounded-sm bg-black/10 px-1 font-mono text-[0.92em]';

function Inline({ text, agent, onRef }: { text: string; agent?: string; onRef?: (id: number) => void }) {
  const parts: ReactNode[] = text.split(INLINE).map((s, i) => {
    if (i % 2 === 0) return s ? <RichText key={i} text={s} agent={agent} onRef={onRef} /> : null;
    if (s.startsWith('`'))
      return (
        <code key={i} className={CODE}>
          <RichText text={s.slice(1, -1)} agent={agent} onRef={onRef} />
        </code>
      );
    return (
      <strong key={i}>
        <Inline text={s.slice(2, -2)} agent={agent} onRef={onRef} />
      </strong>
    );
  });
  return <>{parts}</>;
}

const HEADINGS = ['text-[1.15em]', 'text-[1.08em]', 'text-[1em]', 'text-[1em]'];

export function Markdown({ text, agent, onRef }: { text: string; agent?: string; onRef?: (id: number) => void }) {
  const inline = (t: string) => <Inline text={t} agent={agent} onRef={onRef} />;
  return (
    <>
      {parseBlocks(text).map((b, i) =>
        b.kind === 'code' ? (
          <pre key={i} className={`${CODE} my-1 block overflow-x-auto whitespace-pre p-2 first:mt-0 last:mb-0`}>
            <code>
              <RichText text={b.text} agent={agent} onRef={onRef} />
            </code>
          </pre>
        ) : b.kind === 'heading' ? (
          <div key={i} role="heading" aria-level={b.level} className={`${HEADINGS[b.level - 1]} my-1 font-semibold [overflow-wrap:anywhere] first:mt-0 last:mb-0`}>
            {inline(b.text)}
          </div>
        ) : b.kind === 'list' ? (
          b.ordered ? (
            <ol key={i} className="my-1 list-decimal pl-5 first:mt-0 last:mb-0">
              {b.items.map((t, j) => (
                <li key={j} className="[overflow-wrap:anywhere]">{inline(t)}</li>
              ))}
            </ol>
          ) : (
            <ul key={i} className="my-1 list-disc pl-5 first:mt-0 last:mb-0">
              {b.items.map((t, j) => (
                <li key={j} className="[overflow-wrap:anywhere]">{inline(t)}</li>
              ))}
            </ul>
          )
        ) : (
          <p key={i} className="my-1 whitespace-pre-wrap [overflow-wrap:anywhere] first:mt-0 last:mb-0">
            {inline(b.text)}
          </p>
        ),
      )}
    </>
  );
}
