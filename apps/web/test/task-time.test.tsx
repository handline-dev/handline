// Where a task's time went: one line above the body, opened to a bar and totals and a row per segment from task.time; a daemon without it has no line.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import type { TimeSegment } from '@handline/client';
import { TaskTime } from '#src/TaskTime.tsx';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
let root: Root | null = null;
let box: HTMLElement;
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const line = () => box.querySelector<HTMLElement>('section[aria-label="Time"] > button');

async function mount(s = setup()) {
  location.hash = '#/tasks/1684';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(() => box.querySelector('.tverbs'), 'the actions');
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('task time', () => {
  it('is one line until clicked, then draws the segments in order, adding up to the whole', async () => {
    const s = await mount();
    await until(line, 'the time line');
    expect(line()!.textContent).toMatch(/^Worked .* in all/);
    expect(line()!.textContent).not.toMatch(/up next|queued| [hm]\b/);
    expect(box.querySelector('.ttime')).toBeNull();
    await click(line()!);
    await until(() => box.querySelector('.ttime'), 'the time');
    const kinds = [...box.querySelectorAll<HTMLElement>('[data-segment]')].map((r) => r.dataset.segment);
    expect(kinds).toEqual(['queued', 'working', 'founder', 'working', 'task', 'working', 'agent', 'working', 'account', 'working']);
    const row = (k: string) => box.querySelector(`[data-segment="${k}"]`)!.textContent;
    expect(row('founder')).toContain('needs you');
    expect(row('founder')).not.toContain('needs you');
    expect(row('task')).toContain('blocked by T1688');
    expect(row('agent')).toContain('waiting on handline.auditor');
    expect(row('account')).toContain('at a usage limit');
    expect(row('account')).not.toContain('wall');
    expect(box.querySelector('[data-total="working"]')!.textContent).toContain('3h');
    expect(box.querySelector('[data-total="founder"]')!.textContent).toContain('30m');
    const t = await s.client.call('task.time', { task: 1684 });
    expect(t.segments[0]!.from).toBe(t.start);
    t.segments.slice(1).forEach((x, i) => expect(x.from).toBe(t.segments[i]!.to));
    expect(Number(box.querySelector<HTMLElement>('.ttime')!.dataset.seconds)).toBe(
      (Date.parse(t.segments.at(-1)!.to) - Date.parse(t.start)) / 1000,
    );
  });

  it('words each wait by what it waits on, and each other stretch by its kind', async () => {
    const hour = (n: number) => `2026-10-20T${String(10 + n).padStart(2, '0')}:00:00Z`;
    const seg = (kind: TimeSegment['kind'], on: TimeSegment['on'], n: number): TimeSegment => ({
      kind,
      on,
      text: '',
      task: null,
      from: hour(n),
      to: hour(n + 1),
      seconds: 3600,
    });
    const rows: [TimeSegment, string][] = [
      [seg('working', null, 0), 'Working'],
      [seg('waiting', 'founder', 1), 'Needs you'],
      [seg('waiting', 'task', 2), 'Blocked'],
      [seg('waiting', 'agent', 3), 'Waiting on an agent'],
      [seg('waiting', 'account', 4), 'At a usage limit'],
      [seg('queued', null, 5), 'Waiting to start'],
      [seg('starting', null, 6), 'Starting'],
    ];
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<TaskTime segments={rows.map(([x]) => x)} />));
    const totals = [...box.querySelectorAll<HTMLElement>('[data-total]:not([data-total="all"])')];
    expect(totals.map((t) => t.dataset.total)).toEqual(['working', 'founder', 'task', 'agent', 'account', 'queued', 'starting']);
    rows.forEach(([, word], i) => expect(totals[i]!.textContent).toContain(word));
  });

  it('folds back to the line when clicked again', async () => {
    await mount();
    await until(line, 'the time line');
    await click(line()!);
    await until(() => box.querySelector('.ttime'), 'the time');
    await click(line()!);
    await until(() => !box.querySelector('.ttime'), 'the time to go');
  });

  it('has no line on a daemon that does not serve task.time', async () => {
    const s = setup();
    s.daemon.knobs.unserve('task.time');
    await mount(s);
    expect(line()).toBeNull();
  });
});
