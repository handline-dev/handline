import { describe, expect, it } from 'vitest';
import { addressOf } from '#src/WebTab.tsx';

describe('addressOf', () => {
  it('adds http to a bare host and port', () => expect(addressOf(' localhost:5173 ')).toBe('http://localhost:5173'));
  it('keeps a full address', () => expect(addressOf('https://a.dev/x')).toBe('https://a.dev/x'));
  it('refuses an empty one', () => expect(addressOf('  ')).toBeNull());
});
