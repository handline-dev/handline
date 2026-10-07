import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { DataProvider } from '@handline/app-data';
import { DaemonProvider } from '@handline/app-react';
import { setup } from '@handline/fake-daemon/helpers';
import { Home } from '../src/screens/Home.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect'; // idle with a session
const sleep = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, ms = 4000) {
  for (let t = 0; t < ms; t += 10) {
    if (cond()) return;
    await sleep(10);
  }
  throw new Error(`timed out waiting for ${what}`);
}

let root: Root | null = null;
let box: HTMLElement;
const $ = (id: string) => box.querySelector<HTMLElement>(`[data-testid="${id}"]`);
const text = () => box.textContent ?? '';
/** The height a row is styled with (the DOM has no layout here, so this reads the style both rows are given from the tokens). */
const rowHeightOf = (el: Element | null) => (el ? getComputedStyle(el).height : undefined);
const press = (el: HTMLElement | null) => act(async () => void el?.click());

async function mount(prepare: (s: ReturnType<typeof setup>) => void = () => {}, daemon?: { bytes?: boolean }) {
  const s = setup({ daemon });
  prepare(s);
  const store = new Store(s.client);
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(
      <DaemonProvider store={store}>
        <DataProvider daemonId="test">
          <Home store={store} onUnpair={() => {}} />
        </DataProvider>
      </DaemonProvider>,
    );
  });
  return { ...s, store };
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the agent list', () => {
  it('shows its skeleton after 150 ms while the daemon is slow, then the rows', async () => {
    const m = await mount((s) => s.daemon.knobs.latency(600));
    await until(() => $('skeleton-agents'), 'the skeleton');
    const skeletonRowHeight = rowHeightOf($('skeleton-agents')!.firstElementChild); // the first skeleton row
    expect($(`agent-${ARCH}`)).toBeNull();
    await until(() => $(`agent-${ARCH}`), 'the rows', 5000);
    await sleep(400); // the skeleton stays at least 300 ms once shown
    expect($('skeleton-agents')).toBeNull();
    expect(rowHeightOf($(`agent-${ARCH}`))).toBe(skeletonRowHeight);
    expect(text()).toContain(ARCH);
    m.store.stop();
  });

  it('shows the daemon’s sentence when the read is refused, and Retry reads again', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('agents.list', 'state', 'the daemon is resting', 'try again later'));
    await until(() => $('error'), 'the error block');
    expect(text()).toContain('the daemon is resting (try again later)');
    expect($(`agent-${ARCH}`)).toBeNull();
    m.daemon.knobs.clear();
    await press([...box.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === 'Retry') ?? null);
    await until(() => $(`agent-${ARCH}`), 'the rows after Retry');
    expect($('error')).toBeNull();
    m.store.stop();
  });
});

describe('the agent page', () => {
  it('shows its skeleton, then the controls', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    m.daemon.knobs.latency(500);
    await press($(`agent-${ARCH}`));
    await until(() => $('skeleton-agentDetail'), 'the detail skeleton');
    expect($('back')).not.toBeNull(); // the way out stays while it loads
    await until(() => $('tab-terminal') && $('skeleton-agentDetail') === null, 'the page', 5000);
    m.store.stop();
  });

  it('shows a pending mark at once for a decided write and drops it, with the reason, on a refusal', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('take'), 'Take control');
    const before = $('holder')?.textContent;
    m.daemon.knobs.latency(400);
    m.daemon.knobs.refuse('input.take', 'input_holder', 'someone else holds the input', 'ask them to release');
    await press($('take'));
    await until(() => $('holder')?.textContent?.includes('taking…'), 'the pending mark', 300); // well before the daemon answers (400 ms)
    await until(() => text().includes('someone else holds the input'), 'the reason', 3000);
    await until(() => !$('holder')?.textContent?.includes('taking…'), 'the mark to go');
    expect($('holder')?.textContent).toBe(before); // nothing was faked, so nothing to put back
    m.store.stop();
  });
});

describe('the permission card', () => {
  it('shows "answering…" and keeps its buttons off while the daemon decides, and gives them back with the reason on a refusal', async () => {
    const m = await mount();
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('take'), 'Take control');
    await press($('take'));
    await until(() => $('holder')?.textContent?.includes('YOU'), 'the hold');
    await act(async () => void (await m.store.client.sendInput(ARCH, 'push the branch')));
    await act(async () => void [1, 2, 3].forEach(() => m.daemon.tick())); // the scripted turn: thinking, a line, then the prompt
    await until(() => $('permission-card'), 'the card');
    m.daemon.knobs.latency(400);
    m.daemon.knobs.refuse('prompt.answer', 'state', 'that prompt is not open any more');
    await press($('answer-1'));
    await until(() => $('permission-card')?.textContent?.includes('answering…'), 'the pending mark', 300);
    expect($('answer-1')?.getAttribute('aria-disabled')).toBe('true');
    await until(() => text().includes('that prompt is not open any more'), 'the reason', 3000);
    await until(() => !$('permission-card')?.textContent?.includes('answering…'), 'the mark to go');
    expect($('answer-1')?.getAttribute('aria-disabled')).not.toBe('true');
    m.store.stop();
  }, 20000);
});

describe('typing in the terminal', () => {
  const REFUSAL = 'its screen could not be read';
  async function typing(m: Awaited<ReturnType<typeof mount>>) {
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => $('tab-terminal'), 'the page');
    await press($('tab-terminal'));
    await until(() => $('take'), 'Take control');
    await press($('take'));
    await until(() => $('holder')?.textContent?.includes('YOU'), 'the hold');
    m.daemon.knobs.refuse('session.input', 'screen_blocker', REFUSAL, 'use session.keys or prompt.answer');
    const composer = $('composer') as HTMLInputElement;
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    await act(async () => {
      set.call(composer, 'run the tests');
      composer.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await press([...box.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === 'Send') ?? null);
    return composer;
  }

  it('types as bytes when the daemon serves them, so a refused session.input does not stop it', async () => {
    const m = await mount();
    const composer = await typing(m);
    await until(
      () => m.daemon.sessions.get(ARCH)!.received.some((r) => r.kind === 'text' && r.value === 'run the tests'),
      'the typed line',
    );
    expect($('composer-refused')).toBeNull();
    expect(composer.value).toBe('');
    m.store.stop();
  });

  it('shows the daemon’s refusal where it typed and keeps the text when only session.input is offered', async () => {
    const m = await mount(() => {}, { bytes: false });
    const composer = await typing(m);
    await until(() => $('composer-refused'), 'the refusal');
    expect($('composer-refused')!.textContent).toContain(REFUSAL);
    expect(composer.value).toBe('run the tests');
    m.store.stop();
  });
});
