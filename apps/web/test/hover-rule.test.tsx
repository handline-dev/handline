import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(import.meta.dirname, '../src');
const files = (dir: string): string[] =>
  readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.(tsx?|css)$/.test(f) && f !== 'tokens.css' ? [p] : [];
  });

// The two drag handles tint their bar blue on hover: they are not items, they are the handle itself.
const HANDLES = ['App.tsx', 'Workspace.tsx'];

describe('one hover rule: a light tint from one token', () => {
  it('no component sets its own hover colour, underline, outline or brightness', () => {
    const bad = files(SRC).flatMap((f) =>
      readFileSync(f, 'utf8')
        .split('\n')
        .flatMap((line, i) => {
          const own =
            line.match(/(?<![\w-])(group-)?hover:(?:enabled:)?(bg-(?!hover\b)|text-|underline|outline|border-|brightness-)[\w/[\]-]*/g) ??
            [];
          const ok = (m: string) => HANDLES.some((h) => f.endsWith(h)) && m.startsWith('hover:bg-acc');
          return own.filter((m) => !ok(m)).map((m) => `${f.slice(SRC.length + 1)}:${i + 1} ${m}`);
        }),
    );
    expect(bad).toEqual([]);
  });

  it('a link is not underlined on hover, and the base rule tints it', () => {
    const base = readFileSync(join(SRC, 'styles/base.css'), 'utf8');
    expect(base).not.toMatch(/:hover\s*{[^}]*underline/);
    expect(base).toMatch(/a:hover\s*{[^}]*--hover-bg/);
  });
});
