// The needs-you card in an agent's chat and the kind chip on its rail row.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}

let root: Root | null = null;
let box: HTMLElement;
const card = () => box.querySelector<HTMLElement>('.card-needs');
const chip = () => box.querySelector<HTMLElement>(`[data-agent="${ARCH}"] .needs`);
const rowLine = () => box.querySelector<HTMLElement>(`[data-agent="${ARCH}"] .row-line`);
const alarm = () => box.querySelector<HTMLElement>(`[data-agent="${ARCH}"] .mark.alarm`);

async function mount(s: ReturnType<typeof setup>) {
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
}
let keep = () => {};
afterEach(async () => {
  keep();
  keep = () => {};
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('needs you', () => {
  it('shows the kind, the line and a way to the terminal in the chat, and the kind on the rail row', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined); // the fixture is shared by every setup
    fx.view = {
      needs_you: {
        kind: 'usage',
        since: new Date(Date.now() - 5 * 60_000).toISOString(),
        title: 'Usage limit: choose what to do',
        text: 'Weekly limit reached',
        source: 'session',
      },
    };
    await mount(s);
    await until(() => card(), 'the card');
    expect(card()!.querySelector('strong')!.textContent).toBe('Usage limit: choose what to do');
    expect(card()!.textContent).toContain('Weekly limit reached');
    expect(card()!.querySelector('button')!.textContent).toBe('Open the terminal');
    expect(chip()).toBeNull(); // no amber chip: a warning mark beside the name and the title whole under it
    expect(alarm()).not.toBeNull();
    expect(rowLine()!.textContent).toBe('Usage limit: choose what to do');
  });

  it('a stall the daemon raised shows its title alone on the row and the title with its description in the chat', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = {
      needs_you: {
        kind: 'dialog',
        since: new Date().toISOString(),
        title: 'Stuck at a dialog',
        text: 'cannot get past a dialog: it asks for a login',
        source: 'daemon',
      },
    };
    await mount(s);
    await until(() => card(), 'the card');
    expect(rowLine()!.textContent).toBe('Stuck at a dialog');
    expect(card()!.querySelector('strong')!.textContent).toBe('Stuck at a dialog');
    expect(card()!.textContent).toContain('cannot get past a dialog: it asks for a login');
  });

  it('a start that failed has one button, Try again, in place of the terminal', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = {
      needs_you: {
        kind: 'start_failed',
        since: new Date().toISOString(),
        title: "Won't start",
        text: 'The last 3 starts failed: the repo is no git checkout.',
        source: 'daemon',
      },
    };
    await mount(s);
    await until(() => card(), 'the card');
    expect(card()!.querySelectorAll('button').length).toBe(1);
    expect(card()!.querySelector('button')!.textContent).toBe('Try again');
    expect(card()!.textContent).toContain('The last 3 starts failed');
  });

  describe('when the need goes away', () => {
    const goes = async (source: 'daemon' | 'hook') => {
      const s = setup();
      const a = s.daemon.sessions.get(ARCH)!;
      keep = () => void (a.fx.view = undefined);
      a.fx.view = {
        needs_you: { kind: 'trust', since: new Date().toISOString(), title: 'Waiting for folder trust', text: 'x', source },
      };
      await mount(s);
      await until(() => card(), 'the card');
      a.fx.view = { needs_you: null };
      const script = (s.daemon as unknown as { script: { setState(s: unknown, state: string): void } }).script;
      await act(async () => script.setState(a, a.state === 'idle' ? 'running' : 'idle'));
      await until(() => !card(), 'the card gone');
    };

    it('leaves one grey line for a stall the daemon raised', async () => {
      await goes('daemon');
      await until(() => box.querySelector('.card-solved'), 'the grey line');
      expect(box.querySelector('.card-solved')!.textContent).toMatch(/^Folder trust answered · \d+s$/);
    });

    it('leaves none for a need from a hook or the session', async () => {
      await goes('hook');
      await wait(100);
      expect(box.querySelector('.card-solved')).toBeNull();
    });
  });

  it('shows no card and no chip when the daemon says nothing is waited on', async () => {
    const s = setup();
    await mount(s);
    await wait(300);
    expect(card()).toBeNull();
    expect(chip()).toBeNull();
  });

  it('leaves the answer to the prompt card while a prompt is open', async () => {
    const s = setup();
    const a = s.daemon.sessions.get(ARCH)!;
    keep = () => void (a.fx.view = undefined);
    a.fx.view = {
      needs_you: { kind: 'permission', since: new Date().toISOString(), title: 'Waiting for your OK', text: null, source: 'hook' },
    };
    (s.daemon as unknown as { script: { openPrompt(a: unknown): void } }).script.openPrompt(a);
    await mount(s);
    await wait(300);
    expect(card()).toBeNull();
  });

  it('still shows a dialog card when the daemon sets has_dialog without needs_you', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = { has_dialog: true, needs_you: null, state_detail: 'dialog open' };
    await mount(s);
    await until(() => card(), 'the card');
    expect(card()!.dataset.need).toBe('dialog');
    expect(card()!.querySelector('strong')!.textContent).toBe('Waiting at a dialog');
    expect(card()!.textContent).toContain('dialog open');
    expect(card()!.querySelector('button')!.textContent).toBe('Open the terminal');
  });
});
