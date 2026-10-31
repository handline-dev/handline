// Later on the phone's Requests: from an item's page (quick times, or when a task is done), "Later N" at the foot of the list with when each returns,
// an Undo line, and the PC's entry (NavPrefs.later) written either way.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ARCH, $, box, fill, mount, press, text, until } from '#test/harness.tsx';

const DECISION = 3; // the fake's seeded decision
const DECISION_TASK = 1684; // the founder's task the seeded decision belongs to
const KEY = `item:${DECISION}`;
const row = (key: string) => $(`request-${key}`);
type Setup = Parameters<Parameters<typeof mount>[0] & {}>[0];
const seed = (s: Setup) => {
  s.daemon.tasks.verbs = true;
  const decision = s.daemon.inbox['items'].find((i) => i.id === DECISION);
  if (decision) decision.task = DECISION_TASK;
  s.daemon.tasks.seed({ id: 9400, from: ARCH, to: 'me', title: 'Land the margin', state: 'doing' });
};
const opened = async () => {
  const m = await mount(seed, undefined, 'founder', { opens: 'auto' });
  await until(() => row(KEY), 'the row');
  await press(row(KEY));
  await until(() => $('request-page'), 'the item');
  return m;
};
const entries = (m: Awaited<ReturnType<typeof mount>>) => m.daemon.admin.prefs.get().nav.later as Record<string, unknown>;

describe('Later on Requests', () => {
  afterEach(() => void vi.useRealTimers());

  it('puts the item off from its page with a quick time: the entry is written, the line says when, and the item leaves the list', async () => {
    const m = await opened();
    await press($('request-later'));
    await press($('later-time-0'));
    await until(() => Object.keys(entries(m)).length === 1, 'the entry');
    expect(Object.values(entries(m))[0]).toMatchObject({ until: expect.any(String) });
    await until(() => $('undo-line'), 'the Undo line');
    expect(text($('undo-line'))).toMatch(/^Later: /);
    await until(() => !row(KEY) && !$('request-page'), 'the item off the list');
    m.store.stop();
  });

  it('shows Later N at the foot of the list with when each returns, and opens the item with a way back', async () => {
    const m = await opened();
    await press($('request-later'));
    await press($('later-time-0'));
    await until(() => $('request-back') || $('later-fold'), 'the next item or the list');
    if ($('request-back')) await press($('request-back')); // the next item opened after the put-off
    await until(() => $('later-fold'), 'the fold');
    expect(text($('later-fold-head'))).toBe('Later · 1');
    expect(box.querySelector('[data-testid^="later-row-"]')).toBeNull(); // folded
    await press($('later-fold-head'));
    const later = box.querySelector<HTMLElement>('[data-testid^="later-row-"]')!;
    expect(text(later)).toMatch(/\d\d:\d\d/);
    await press(later);
    await until(() => $('request-page'), 'the item');
    expect(text($('request-position'))).toBe('Later');
    expect(text($('request-later'))).toMatch(/^Later: /);
    await press($('request-return'));
    await until(() => Object.values(entries(m)).every((v) => v === null) || !Object.keys(entries(m)).length, 'the entry cleared');
    await until(() => text($('request-position')) !== 'Later', 'the item back in the list');
    m.store.stop();
  });

  it('puts the item off until a task is done', async () => {
    const m = await opened();
    await press($('request-later'));
    await press($('later-by-task'));
    await press($('later-task-9400'));
    await until(() => Object.values(entries(m)).some((v) => (v as { after_task?: number } | null)?.after_task === 9400), 'the entry');
    m.store.stop();
  });

  it('puts the item off until a time of its own, and a put-off item changes its time from the same line', async () => {
    const m = await opened();
    await press($('request-later'));
    await press($('later-by-time'));
    await fill('later-day', '2031-03-04');
    await fill('later-clock', '09:30');
    await press($('later-pick-put'));
    await until(
      () => Object.values(entries(m)).some((v) => (v as { until?: string } | null)?.until?.startsWith('2031-03-04')),
      'the entry',
    );
    await until(() => $('request-back') || $('later-fold'), 'the next item or the list');
    if ($('request-back')) await press($('request-back'));
    await until(() => $('later-fold'), 'the fold');
    await press($('later-fold-head'));
    await press(box.querySelector<HTMLElement>('[data-testid^="later-row-"]'));
    await until(() => text($('request-later')).startsWith('Later: '), 'the put-off page');
    await press($('request-later'));
    await press($('later-time-0'));
    await until(() => Object.values(entries(m)).some((v) => !(v as { until?: string } | null)?.until?.startsWith('2031')), 'the new time');
    expect($('request-page')).not.toBeNull(); // changing the time keeps the page
    m.store.stop();
  });

  it('refuses a time that is not ahead', async () => {
    const m = await opened();
    await press($('request-later'));
    await press($('later-by-time'));
    await fill('later-day', '2001-01-01');
    expect($('later-pick-put')?.getAttribute('aria-disabled')).toBe('true');
    m.store.stop();
  });

  it('Undo puts the item back as it was', async () => {
    const m = await opened();
    await press($('request-later'));
    await press($('later-time-0'));
    await until(() => $('undo-line'), 'the Undo line');
    await press($('undo'));
    await until(() => Object.values(entries(m)).every((v) => v === null), 'the entry cleared');
    await until(() => !$('undo-line'), 'the line gone');
    m.store.stop();
  });
});
