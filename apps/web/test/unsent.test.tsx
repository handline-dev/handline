import { afterEach, expect, it } from 'vitest';
import { holdsUnsent } from '#src/shell.ts';

afterEach(() => {
  document.body.innerHTML = '';
});

it('a restart is held for an open dialog, typed text, or a row the daemon has not numbered, and for nothing else', () => {
  expect(holdsUnsent(document)).toBe(false);
  document.body.innerHTML = '<textarea></textarea><input type="text" value="  ">';
  expect(holdsUnsent(document)).toBe(false);
  document.body.innerHTML = '<textarea>hello</textarea>';
  expect(holdsUnsent(document)).toBe(true);
  document.body.innerHTML = '<div data-id="-1"></div>';
  expect(holdsUnsent(document)).toBe(true);
  document.body.innerHTML = '<div data-id="12"></div>';
  expect(holdsUnsent(document)).toBe(false);
  document.body.innerHTML = '<div data-pending></div>';
  expect(holdsUnsent(document)).toBe(true);
  document.body.innerHTML = '<div aria-modal="true"></div>';
  expect(holdsUnsent(document)).toBe(true);
});
