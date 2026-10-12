// G05: a screen draws a symbol with Icon or State, never with a character. Letters, digits, punctuation, spaces and · … ⌘ ↵ are text.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const src = join(import.meta.dirname, '../src');
const files = [src, join(src, 'ui')]
  .flatMap((d) => readdirSync(d).map((f) => join(d, f)))
  .filter((f) => /\.tsx?$/.test(f) && !f.endsWith('Gallery.tsx'));

const code = (s: string) =>
  s
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\s\/\/ .*$/gm, '');

describe('characters', () => {
  it('no screen file holds a character outside the allow list', () => {
    const bad = files.flatMap((f) =>
      [...code(readFileSync(f, 'utf8')).matchAll(/[^\p{ASCII}\p{L}\p{N}\p{P}\p{Z}·…⌘↵]/gu)].map((m) => `${f.slice(src.length + 1)} ${m[0]}`),
    );
    expect([...new Set(bad)]).toEqual([]);
  });
});
