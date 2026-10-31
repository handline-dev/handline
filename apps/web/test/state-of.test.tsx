import { expect, it } from 'vitest';
import { stateOf } from '#src/ui/index.ts';

it("maps the daemon's state words, and a word it does not know is idle", () => {
  expect(stateOf('running')).toBe('working');
  expect(stateOf('WAITING')).toBe('needs');
  expect(stateOf('blocked')).toBe('blocked');
  expect(stateOf('parked')).toBe('parked'); // its own shape: a hollow ring would be idle's
  expect(stateOf('stopped')).toBe('idle');
  expect(stateOf('done')).toBe('done');
  expect(stateOf('pending')).toBe('idle'); // a pending task waits on the daemon, not on a person
  expect(stateOf('queued')).toBe('booting');
  expect(stateOf('something new')).toBe('idle');
});
