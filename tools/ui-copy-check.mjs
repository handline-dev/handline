// Fails when a user-visible string in the apps holds an internal word (tools/ui-copy-banlist.json).
//   node tools/ui-copy-check.mjs [<file> ...]   (default: every .ts and .tsx under apps/*/src and packages/*/src)
// User-visible: JSX text, the title, aria-label, placeholder, label, alt and hint props, and the label, title, text, hint, name,
// placeholder, message and word keys of an object, and an object value that is nothing but a banned phrase (a word table). A line ending in `// copy-ok: <why>` is skipped.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import ts from 'typescript';

const here = new URL('.', import.meta.url).pathname;
const { ban } = JSON.parse(readFileSync(join(here, 'ui-copy-banlist.json'), 'utf8'));
const rules = ban.map((b) => ({ re: new RegExp(b.re), say: b.say }));
const PROPS = new Set(['title', 'aria-label', 'placeholder', 'label', 'alt', 'hint']);
const KEYS = new Set(['label', 'title', 'text', 'hint', 'name', 'placeholder', 'message', 'word']);

const walk = (dir) =>
  readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (n === 'node_modules') return [];
    return statSync(p).isDirectory() ? walk(p) : /\.tsx?$/.test(n) ? [p] : [];
  });
const root = join(here, '..');
const files = process.argv.length > 2 ? process.argv.slice(2) : ['apps', 'packages'].flatMap((g) => readdirSync(join(root, g)).map((a) => [g, a])).flatMap(([g, a]) => {
  try { return walk(join(root, g, a, 'src')); } catch { return []; }
});

let bad = 0;
for (const file of files) {
  const text = readFileSync(file, 'utf8');
  const src = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const lines = text.split('\n');
  // `whole`: a string anywhere counts only when it is the banned phrase and nothing else (a word table: queued: 'Queued'; a compared literal is not shown).
  const check = (node, s, whole = false) => {
    const { line } = src.getLineAndCharacterOfPosition(node.getStart());
    if (/copy-ok:/.test(lines[line] ?? '')) return;
    for (const r of rules) if (whole ? s.trim() === r.re.exec(s)?.[0] : r.re.test(s)) (bad++, console.log(`${file}:${line + 1}: "${s.trim().slice(0, 70)}" holds ${r.re.source}; say: ${r.say}`));
  };
  // Every string under a user-visible position: a conditional or a template in a prop holds several.
  const strings = (node, f) => {
    if (ts.isStringLiteralLike(node)) f(node, node.text);
    else if (ts.isTemplateExpression(node)) (f(node.head, node.head.text), node.templateSpans.forEach((sp) => f(sp.literal, sp.literal.text)));
    ts.forEachChild(node, (c) => strings(c, f));
  };
  const visit = (node) => {
    if (ts.isJsxText(node)) check(node, node.text);
    else if (ts.isJsxAttribute(node) && PROPS.has(node.name.getText()) && node.initializer) strings(node.initializer, check);
    else if (ts.isPropertyAssignment(node)) {
      if (KEYS.has(node.name.getText().replace(/['"]/g, ''))) strings(node.initializer, check);
      else if (ts.isStringLiteralLike(node.initializer)) check(node.initializer, node.initializer.text, true);
    }
    ts.forEachChild(node, visit);
  };
  visit(src);
}
if (bad) (console.log(`${bad} user-visible string(s) hold an internal word`), process.exit(1));
