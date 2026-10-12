import { describe, expect, it } from 'vitest';
import { $, mount, until } from './harness.tsx';
import { StateMark } from '../src/screens/StateMark.tsx';

describe('the state mark', () => {
  it('reads an open dialog or a block over the daemon state word, so a running row that waits shows waiting', async () => {
    const rows = [
      { state: 'running', has_dialog: true, block: null },
      { state: 'running', has_dialog: false, block: 'rate_limit' },
      { state: 'running', has_dialog: false, block: null },
    ] as const;
    const m = await mount(undefined, undefined, undefined, {
      screen: () => (
        <>
          {rows.map((r, i) => (
            <StateMark key={i} agent={r as never} />
          ))}
        </>
      ),
    });
    await until(() => $('state-waiting'), 'the marks');
    expect($('state-blocked')).not.toBeNull();
    expect($('state-running')).not.toBeNull();
    m.store.stop();
  });
});
