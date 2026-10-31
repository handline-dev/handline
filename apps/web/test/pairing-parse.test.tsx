import { expect, it } from 'vitest';
import { codeFrom, deviceName, inviteCode, needsToken } from '#src/pairing.ts';

it('reads the code out of a pasted line or an invite link, knows which urls want a token, and names the browser', () => {
  expect(codeFrom('  ABCD-1234 ')).toBe('ABCD-1234');
  expect(codeFrom('handline://pair?host=127.0.0.1:7443&code=ABCD-1234')).toBe('ABCD-1234');
  expect(needsToken('ws://127.0.0.1:7443/rpc')).toBe(true);
  expect(needsToken('ws://127.0.0.1:7777')).toBe(false);
  expect(inviteCode('#pair=K7QM-2XPD')).toBe('K7QM-2XPD');
  expect(inviteCode('#/agents/x')).toBeNull();
  expect(inviteCode('')).toBeNull();
  expect(deviceName('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36')).toBe('Chrome on Mac');
  expect(deviceName('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17 Safari/605.1.15')).toBe(
    'Safari on Mac',
  );
});
