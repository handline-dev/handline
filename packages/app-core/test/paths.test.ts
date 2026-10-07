import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findPaths } from '../src/index.ts';

const found = (s: string) => findPaths(s).map((p) => [s.slice(p.start, p.end), p.path]);

test('a home path, an absolute path with an extension and a relative path with a slash or an extension are found', () => {
  assert.deepEqual(found('see ~/notes/a.md and /var/log/out.log'), [
    ['~/notes/a.md', '~/notes/a.md'],
    ['/var/log/out.log', '/var/log/out.log'],
  ]);
  assert.deepEqual(found('edit docs/design/x.md, then README.md.'), [
    ['docs/design/x.md', 'docs/design/x.md'],
    ['README.md', 'README.md'],
  ]);
});

test('a line and column are dropped from what the shell is given but stay in the text', () => {
  assert.deepEqual(found('at src/a.ts:42:7 it fails'), [['src/a.ts:42:7', 'src/a.ts']]);
  assert.deepEqual(found('see /x/b.py:9'), [['/x/b.py:9', '/x/b.py']]);
});

test('plain words, numbers, urls and emails are not paths', () => {
  assert.deepEqual(found('hello world 3.14 and v2'), []);
  assert.deepEqual(found('https://example.com/a/b.html'), []);
  assert.deepEqual(found('mail user@example.com'), []);
});
