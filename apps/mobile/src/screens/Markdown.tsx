// A Markdown file drawn as text: headings, paragraphs, lists, quotes, fenced code, and **bold**, *italic* and `code` inside a line. Nothing else
// is interpreted (no HTML, no images, no links followed), so a file from an agent can only show text.
import { Fragment, type ReactNode } from 'react';
import { Text, View } from 'react-native';
import { isCommandLang } from '@handline/app-core';
import { font, radius, space } from '@handline/tokens';
import { CommandCard } from '#src/screens/CommandCard.tsx';
import { usePalette } from '#src/theme.ts';

type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'item'; mark: string; text: string }
  | { kind: 'quote' | 'paragraph'; text: string }
  // `command`: its place among the message's command blocks (sh, bash, shell), counted from 0; absent for any other fence.
  | { kind: 'code'; text: string; lang: string; command?: number };

export function blocks(md: string, breaks = false): Block[] {
  const out: Block[] = [];
  const lines = md.replace(/\r\n?/g, '\n').split('\n');
  let para: string[] = [];
  let commands = 0;
  const flush = () => {
    if (para.length) out.push({ kind: 'paragraph', text: para.join(breaks ? '\n' : ' ') });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i]!;
    if (/^```/.test(l)) {
      flush();
      const lang = l.slice(3).trim();
      const code: string[] = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]!); i++) code.push(lines[i]!);
      out.push({ kind: 'code', text: code.join('\n'), lang, ...(isCommandLang(lang) ? { command: commands++ } : {}) });
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(l);
    const li = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(l);
    const q = /^>\s?(.*)$/.exec(l);
    if (h) {
      flush();
      out.push({ kind: 'heading', level: h[1]!.length, text: h[2]! });
    } else if (li) {
      flush();
      out.push({ kind: 'item', mark: /\d/.test(li[1]!) ? li[1]! : '•', text: li[2]! });
    } else if (q) {
      flush();
      out.push({ kind: 'quote', text: q[1]! });
    } else if (!l.trim()) flush();
    else para.push(l.trim());
  }
  flush();
  return out;
}

/** How a stretch of plain words is drawn, so a caller can link something in it. */
export type Plain = (s: string) => ReactNode;

/** One line with its `code`, **bold** and *italic* spans. */
function Inline({ text, plain }: { text: string; plain?: Plain }) {
  const c = usePalette();
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g).filter(Boolean);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('`') ? (
          <Text key={i} style={{ fontFamily: 'Menlo', backgroundColor: c.line }}>
            {p.slice(1, -1)}
          </Text>
        ) : p.startsWith('**') ? (
          <Text key={i} style={{ fontWeight: '700' }}>
            {p.slice(2, -2)}
          </Text>
        ) : p.startsWith('*') ? (
          <Text key={i} style={{ fontStyle: 'italic' }}>
            {p.slice(1, -1)}
          </Text>
        ) : plain ? (
          <Fragment key={i}>{plain(p)}</Fragment>
        ) : (
          p
        ),
      )}
    </>
  );
}

/** `breaks` keeps a line break inside a paragraph (a chat message); `plain` draws the plain words; `selectable` lets a long press select and copy them. */
export function Markdown({
  text,
  testID,
  breaks,
  plain,
  selectable,
  cards,
}: {
  text: string;
  testID?: string;
  breaks?: boolean;
  plain?: Plain;
  selectable?: boolean;
  /** Command fences are cards with Copy and Run (a chat message or a comment), not plain code. */
  cards?: boolean;
}) {
  const c = usePalette();
  return (
    <View testID={testID} style={{ gap: space.sm }}>
      {blocks(text, breaks).map((b, i) =>
        b.kind === 'code' && b.command !== undefined && cards ? (
          <CommandCard key={i} text={b.text} index={b.command} />
        ) : b.kind === 'code' ? (
          <View key={i} style={{ backgroundColor: c.line, borderRadius: radius.m, padding: space.sm }}>
            <Text selectable={selectable} style={{ color: c.ink, fontFamily: 'Menlo', fontSize: font.size.sm }}>
              {b.text}
            </Text>
          </View>
        ) : b.kind === 'item' ? (
          <View key={i} style={{ flexDirection: 'row', gap: space.xs }}>
            <Text style={{ color: c.ink }}>{b.mark}</Text>
            <Text selectable={selectable} style={{ color: c.ink, flexShrink: 1 }}>
              <Inline text={b.text} plain={plain} />
            </Text>
          </View>
        ) : (
          <Text
            key={i}
            selectable={selectable}
            accessibilityRole={b.kind === 'heading' ? 'header' : undefined}
            style={{
              color: b.kind === 'quote' ? c.muted : c.ink,
              ...(b.kind === 'heading'
                ? { fontWeight: '700', fontSize: b.level === 1 ? font.size.xl : b.level === 2 ? font.size.lg : font.size.md }
                : {}),
              ...(b.kind === 'quote' ? { borderLeftWidth: 2, borderLeftColor: c.line, paddingLeft: space.sm } : {}),
            }}
          >
            <Inline text={b.text} plain={plain} />
          </Text>
        ),
      )}
    </View>
  );
}
