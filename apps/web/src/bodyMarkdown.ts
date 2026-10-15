// Stored task bodies are plain Markdown written by people and agents, so the editor must give back what it was given. Tiptap parses raw HTML,
// decodes `&amp;` and escapes `_` in paths when it saves; these two functions keep the text as written.

/** The stored text as the editor should read it: `<` and `&` are text, not HTML. */
export const forEditor = (md: string) => md.replace(/([<&])/g, '\\$1');

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
