// The Tasks tab's My requests chip: the founder's own open tasks in their groups, each with one line (how far, who works now, or what it waits on).
import { describe, expect, it } from 'vitest';
import { until, $, text, press, mount } from '#test/harness.tsx';
import type { setup } from '@handline/fake-daemon/helpers';

const seed = (s: ReturnType<typeof setup>) => {
  s.daemon.tasks.seed({ id: 7110, from: 'human', to: 'handline.daemon-core', title: 'Keyboard on the request screen', state: 'doing' });
  s.daemon.tasks.seed({ id: 7031, from: 'human', to: 'handline.pc-chat', title: 'Decide the status line', state: 'doing' });
  s.daemon.tasks.seed({
    id: 7121,
    from: 'human',
    to: 'handline.pc-chat',
    title: 'Status line shape',
    state: 'doing',
    blockers: [{ task: 7031, reason: '' }],
  });
  s.daemon.requests.add({
    title: 'Keyboard on the request screen',
    task: 7110,
    open: 2,
    done: 3,
    holders: ['handline.daemon-core'],
    now: 'handline.daemon-core',
  });
  s.daemon.requests.add({
    title: 'Status line shape',
    task: 7121,
    open: 1,
    done: 0,
    holders: ['handline.pc-chat'],
    now: 'handline.pc-chat',
    blocked: true,
  });
  s.daemon.requests.add({ title: 'Closed tasks by day', task: 7133, open: 0, done: 0 });
  s.daemon.requests.add({ title: 'Settings left bar', task: 7102, status: 'waiting_on_you', open: 1, done: 4 });
  s.daemon.requests.add({
    title: 'Which build is live',
    task: 6020,
    status: 'waiting_on_you',
    open: 1,
    done: 0,
    now: 'handline',
    holders: ['handline'],
  });
  s.daemon.requests.add({ title: 'Already done', task: 7001, status: 'done', done: 2 });
};
const openList = async () => {
  const m = await mount(seed, undefined, 'founder');
  await press($('nav-tasks'));
  await until(() => $('my-requests-chip'), 'the chip');
  await until(() => text($('my-requests-chip')).includes('5'), 'the count');
  await press($('my-requests-chip'));
  await until(() => $('my-requests'), 'the list');
  return m;
};

describe('the My requests chip', () => {
  it('counts the open requests and lists them in their groups with their line', async () => {
    const m = await openList();
    const group = (name: string) => document.querySelector(`[aria-label="${name}"]`);
    expect(text(group('Waiting on you') as HTMLElement)).toContain('Settings left bar');
    expect(text($('my-request-7102'))).toContain('waiting on you'); // not 'not taken yet'
    expect(text($('my-request-6020'))).toContain('0 of 1 done · waiting on you'); // in this group the line says so, not who works now
    expect(text($('my-request-7110'))).toContain('3 of 5 done · now daemon-core');
    expect(text($('my-request-7133'))).toContain('not taken yet');
    expect(text(group('Blocked') as HTMLElement)).toContain('Status line shape');
    expect($('my-request-7001')).toBeNull(); // a done one is not open
    m.store.stop();
  });

  it('says what a blocked request waits on, and the id opens it', async () => {
    const m = await openList();
    expect(text($('my-request-blocked'))).toMatch(/^Blocked on\s?T7031$/); // local-ref-ok
    await press($('my-request-blocker'));
    await until(() => text().includes('Decide the status line'), 'the blocker open');
    m.store.stop();
  });

  it('opens a request as a row of Tasks does, and the × takes the chip off', async () => {
    const m = await openList();
    await press($('my-request-7110'));
    await until(() => text().includes('Keyboard on the request screen'), 'the task');
    m.store.stop();
    const n = await openList();
    await press($('my-requests-chip'));
    await until(() => !$('my-requests'), 'the list off');
    n.store.stop();
  });

  it('turns off when Assigned to me or Closed is picked', async () => {
    for (const scope of ['mine', 'closed']) {
      const m = await openList();
      await press($(`scope-${scope}`));
      await until(() => !$('my-requests'), 'the list off');
      m.store.stop();
    }
  });
});
