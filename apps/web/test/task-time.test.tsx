// Where a task's time went: one line above the body, opened to a bar and totals and a row per segment from task.time; a daemon without it has no line.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
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
    expect(line()!.textContent).toMatch(/worked .* in all/);
    expect(box.querySelector('.ttime')).toBeNull();
    await click(line()!);
    await until(() => box.querySelector('.ttime'), 'the time');
    const kinds = [...box.querySelectorAll<HTMLElement>('[data-segment]')].map((r) => r.dataset.segment);
    expect(kinds).toEqual(['queued', 'working', 'waiting', 'working']);
    expect(box.querySelector('[data-segment="waiting"]')!.textContent).toContain('waiting on you');
    expect(box.querySelector('[data-total="working"]')!.textContent).toContain('3 h 30 m');
    expect(box.querySelector('[data-total="waiting"]')!.textContent).toContain('30 m');
    const t = await s.client.call('task.time', { task: 1684 });
    expect(t.segments[0]!.from).toBe(t.start);
    t.segments.slice(1).forEach((x, i) => expect(x.from).toBe(t.segments[i]!.to));
    expect(Number(box.querySelector<HTMLElement>('.ttime')!.dataset.seconds)).toBe(
      (Date.parse(t.segments.at(-1)!.to) - Date.parse(t.start)) / 1000,
    );
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
