// Stored task bodies are plain Markdown written by people and agents, so the editor must give back what it was given. Tiptap parses raw HTML,
// decodes `&amp;` and escapes `_` in paths when it saves; these two functions keep the text as written.

const IMAGE = String.raw`!\[[^\]\n]*\]\(attachment:[^)\s]+\)`;

/** The stored text as the editor should read it: `<` and `&` are text, not HTML. An image after a single newline gets its own paragraph, or an edit drops it. */
export const forEditor = (md: string) =>
  md
    .replace(/([<&])/g, '\\$1')
    .replace(new RegExp(`([^\\n])\\n(?=${IMAGE})`, 'g'), '$1\n\n')
    .replace(new RegExp(`(${IMAGE})\\n(?=[^\\n])`, 'g'), '$1\n\n');

/** Escapes only what would turn into formatting again: `_` between two word characters (snake_case, paths) and `<`, `&` are left as typed. */
export function escapeText(text: string): string {
  return text.replace(/\\|[`*[\]~]|(?<![\w/.-])_|_(?![\w/.-])/g, (m) => `\\${m}`);
}

type Encoder = { encodeTextForMarkdown: (text: string, node: { marks?: { type: string }[] }, parent?: { type?: string }) => string };

/** Makes the editor save text as typed: code is left alone, the rest escapes only what `escapeText` says. */
export function keepTyped(editor: { markdown?: unknown }) {
  const m = editor.markdown as Encoder | undefined;
  if (m) m.encodeTextForMarkdown = (t, n, p) => (p?.type === 'codeBlock' || n.marks?.some((k) => k.type === 'code') ? t : escapeText(t));
}

/** `%20` read as a space; text that is not a valid escape (a stray `%`) is left as written. */
const unescapeName = (s: string) => {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
};

/** A Markdown file's pictures drawn from the memo's own image files: `![alt](any/path/x.png)` becomes the attachment named `x.png`; one with no such file is its alt text. */
export function resolveImages(md: string, files: { id: string; name: string }[]): string {
  return md.replace(/!\[([^\]\n]*)\]\(([^)\s]+)[^)]*\)/g, (whole, alt: string, src: string) => {
    if (src.startsWith('attachment:')) return whole;
    const raw = src.split(/[?#]/)[0]!.split('/').pop() ?? '';
    const hit = files.find((f) => f.name === raw || f.name === unescapeName(raw));
    return hit ? `![${alt}](attachment:${hit.id})` : alt;
  });
}
