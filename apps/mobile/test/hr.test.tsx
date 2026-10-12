import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { DataProvider } from '@handline/app-data';
import { DaemonProvider } from '@handline/app-react';
import { setup } from '@handline/fake-daemon/helpers';
import type { FakeHr } from '../../../packages/fake-daemon/src/hr.ts';
import { TeamEntry } from '../src/screens/TeamEntry.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
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
const press = (el: HTMLElement | null) => act(async () => void el?.click());
const fill = (id: string, v: string) =>
  act(async () => {
    const f = $(id) as unknown as HTMLInputElement;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(f, v);
    f.dispatchEvent(new Event('input', { bubbles: true }));
  });
const value = (id: string) => ($(id) as unknown as HTMLInputElement | null)?.value;
const hrOf = (s: ReturnType<typeof setup>) => (s.daemon as unknown as { script: { hr: FakeHr } }).script.hr;
const raise = (s: ReturnType<typeof setup>, id: number, over: object = {}) =>
  hrOf(s).raise({
    id,
    title: `settings ${ARCH}`,
    agent: ARCH,
    diff: '- effort  high\n+ effort  max',
    reason: 'it needs more thought',
    ...over,
  });
const stateOf = (s: ReturnType<typeof setup>, id: number) => hrOf(s).find(id).state;

async function mount(prepare: (s: ReturnType<typeof setup>) => void = () => {}, role: 'founder' | 'member' = 'founder') {
  const s = setup({ role });
  prepare(s);
  const store = new Store(s.client);
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(
      <DaemonProvider store={store}>
        <DataProvider daemonId="test">
          <TeamEntry />
        </DataProvider>
      </DaemonProvider>,
    );
  });
  return { ...s, store };
}
async function openHr() {
  await until(() => $('team-open'), 'the entry');
  await press($('team-open'));
  await until(() => $('team-tab-hr'), 'the HR tab');
  await press($('team-tab-hr'));
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the team page', () => {
  it('lists the team with who may ask whom, and opens a card for its edges', async () => {
    const m = await mount();
    await press($('team-open'));
    await until(() => $(`team-${ARCH}`), 'the cards');
    expect(text()).toContain('Top of the team');
    expect($(`team-edges-${ARCH}`)).toBeNull();
    await press($(`team-${ARCH}`));
    expect($(`team-edges-${ARCH}`)?.textContent).toContain('may ask');
    await press($(`team-${ARCH}`));
    expect($(`team-edges-${ARCH}`)).toBeNull();
    m.store.stop();
  });

  it('shows its skeleton while the daemon is slow', async () => {
    const m = await mount((s) => s.daemon.knobs.latency(600));
    await press($('team-open'));
    await until(() => $('skeleton-team'), 'the skeleton');
    expect($(`team-${ARCH}`)).toBeNull();
    await until(() => $(`team-${ARCH}`), 'the cards', 6000);
    m.store.stop();
  });

  it('says so when the daemon refuses team.get, and Retry brings the list', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('team.get', 'state', 'the team is resting'));
    await press($('team-open'));
    await until(() => $('error'), 'the error');
    expect(text()).toContain('the team is resting');
    m.daemon.knobs.clear();
    await press([...box.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === 'Retry') ?? null);
    await until(() => $(`team-${ARCH}`), 'the cards');
    m.store.stop();
  });
});

describe('the HR list', () => {
  it('says "Nothing to decide" when there are no proposals', async () => {
    const m = await mount();
    await openHr();
    await until(() => $('empty'), 'the empty sentence');
    expect($('empty')?.textContent).toBe('Nothing to decide.');
    m.store.stop();
  });

  it('shows its skeleton while slow, then the waiting count on the entry and the cards with a collapsed diff', async () => {
    const m = await mount((s) => {
      raise(s, 1);
      raise(s, 2, { state: 'apply_failed', error: 'boom' });
      raise(s, 3, { state: 'approved' });
      s.daemon.knobs.latency(400);
    });
    await openHr();
    await sleep(250);
    expect($('hr-list')).toBeNull();
    await until(() => $('hr-1'), 'the cards', 6000);
    expect(text()).toContain('HR · 2 waiting');
    expect(text()).toContain('boom');
    expect($('hr-diff-1')).toBeNull();
    expect($('hr-diff-toggle-1')?.textContent).toBe('Show diff (2 changes)');
    await press($('hr-diff-toggle-1'));
    expect($('hr-diff-1')?.textContent).toContain('+ effort  max');
    expect($('hr-approve-2')?.textContent).toBe('Retry');
    expect($('hr-approve-3')).toBeNull(); // settled: no verbs
    m.store.stop();
  });

  it('draws the verbs for a paired member too', async () => {
    const m = await mount((s) => raise(s, 1), 'member');
    await openHr();
    await until(() => $('hr-1'), 'the card');
    expect($('hr-approve-1')).not.toBeNull();
    expect(text()).not.toContain('Decide on a computer.');
    m.store.stop();
  });

  it('shows the reason and keeps the card when the daemon refuses an approve, then approves', async () => {
    const m = await mount((s) => raise(s, 1));
    await openHr();
    await until(() => $('hr-1'), 'the card');
    m.daemon.knobs.refuse('hr.decide', 'state', 'the roster is locked');
    await press($('hr-approve-1'));
    await until(() => text().includes('the roster is locked'), 'the reason', 3000);
    expect(stateOf(m, 1)).toBe('open');
    expect($('hr-approve-1')?.hasAttribute('disabled')).toBe(false);
    m.daemon.knobs.clear();
    await press($('hr-approve-1'));
    await until(() => stateOf(m, 1) === 'approved', 'the daemon');
    await until(() => $('hr-approve-1') === null, 'the verbs to go');
    m.store.stop();
  });

  it('says "This proposal changed" when it changed under the click, and sends nothing for the old text', async () => {
    const m = await mount((s) => raise(s, 1));
    await openHr();
    await until(() => $('hr-1'), 'the card');
    hrOf(m).find(1).subject = 'changed-elsewhere';
    await press($('hr-approve-1'));
    await until(() => $('hr-note-1'), 'the note');
    expect($('hr-note-1')?.textContent).toBe('This proposal changed');
    expect(stateOf(m, 1)).toBe('open');
    m.store.stop();
  });

  it('rejects with a reason only, and keeps the reason through a refusal', async () => {
    const m = await mount((s) => raise(s, 1));
    await openHr();
    await until(() => $('hr-1'), 'the card');
    await press($('hr-reject-1'));
    await press($('hr-reject-go'));
    expect($('hr-reject-error')?.textContent).toBe('A reason is required.');
    await fill('hr-reject-why', 'not now');
    m.daemon.knobs.refuse('hr.decide', 'state', 'the roster is locked');
    await press($('hr-reject-go'));
    await until(() => text().includes('the roster is locked'), 'the reason', 3000);
    expect(value('hr-reject-why')).toBe('not now');
    m.daemon.knobs.clear();
    await press($('hr-reject-go'));
    await until(() => stateOf(m, 1) === 'rejected', 'the daemon');
    await until(() => $('hr-reject-sheet') === null, 'the sheet to close');
    m.store.stop();
  });

  it('approves all after a question, and says how many when one fails to apply', async () => {
    const m = await mount((s) => {
      raise(s, 1);
      raise(s, 2);
      raise(s, 3);
      hrOf(s).failApply.set(2, 'cannot apply');
    });
    await openHr();
    await until(() => $('hr-all'), 'Approve all');
    await press($('hr-all'));
    expect(text()).toContain('Approve all 3?');
    await press($('hr-all-cancel'));
    expect(stateOf(m, 1)).toBe('open');
    await press($('hr-all'));
    await press($('hr-all-go'));
    await until(() => stateOf(m, 1) === 'approved' && stateOf(m, 3) === 'approved', 'the daemon');
    expect(stateOf(m, 2)).toBe('apply_failed');
    m.store.stop();
  });

  it('a second tap on Approve all while it runs sends nothing', async () => {
    const m = await mount((s) => {
      raise(s, 1);
      raise(s, 2);
      raise(s, 3);
    });
    await openHr();
    await until(() => $('hr-all'), 'Approve all');
    await press($('hr-all'));
    const find = vi.spyOn(hrOf(m), 'find');
    await act(async () => {
      $('hr-all-go')?.click();
      $('hr-all-go')?.click();
    });
    await until(() => hrOf(m).proposals[2]!.state === 'approved', 'the daemon');
    expect(find).toHaveBeenCalledTimes(3);
    m.store.stop();
  });

  it('rejects all with one reason, asked for first', async () => {
    const m = await mount((s) => {
      raise(s, 1);
      raise(s, 2);
      raise(s, 3);
    });
    await openHr();
    await until(() => $('hr-reject-all'), 'Reject all');
    await press($('hr-reject-all'));
    expect(text()).toContain('Reject all 3?');
    expect(($('hr-reject-all-go') as unknown as HTMLButtonElement).disabled).toBe(true);
    await press($('hr-reject-all-cancel'));
    expect(stateOf(m, 1)).toBe('open');
    await press($('hr-reject-all'));
    await fill('hr-reject-all-why', 'not now');
    await press($('hr-reject-all-go'));
    await until(() => [1, 2, 3].every((id) => stateOf(m, id) === 'rejected'), 'the daemon');
    m.store.stop();
  });

  it('withdraws after its confirmation', async () => {
    const m = await mount((s) => raise(s, 1));
    await openHr();
    await until(() => $('hr-1'), 'the card');
    await press($('hr-withdraw-1'));
    await press($('hr-withdraw-cancel'));
    expect(stateOf(m, 1)).toBe('open');
    await press($('hr-withdraw-1'));
    await press($('hr-withdraw-go'));
    await until(() => stateOf(m, 1) === 'withdrawn', 'the daemon');
    m.store.stop();
  });

  it('edits the proposal’s agent settings before approving, sends only what changed, and keeps the sheet when refused', async () => {
    const m = await mount((s) => raise(s, 1));
    await openHr();
    await until(() => $('hr-1'), 'the card');
    await press($('hr-settings-1'));
    await until(() => $('hr-settings'), 'the sheet');
    const was = { ...m.daemon.sessions.get(ARCH)!.settings };
    await press($('hs-effort-max'));
    m.daemon.knobs.refuse('agent.set', 'state', 'that agent is mid-launch');
    await press($('hs-save'));
    await until(() => $('hs-error')?.textContent?.includes('that agent is mid-launch'), 'the reason', 3000);
    expect($('hr-settings')).not.toBeNull();
    expect(m.daemon.sessions.get(ARCH)!.settings.effort).toBe(was.effort);
    m.daemon.knobs.clear();
    await press($('hs-save'));
    await until(() => $('hr-settings') === null, 'the sheet to close');
    expect(m.daemon.sessions.get(ARCH)!.settings).toEqual({ ...was, effort: 'max' });
    expect(stateOf(m, 1)).toBe('open'); // saving settings is not deciding
    await press($('hr-approve-1'));
    await until(() => stateOf(m, 1) === 'approved', 'the daemon');
    m.store.stop();
  });

  it('asks before a danger switch, and refuses a bad number without sending', async () => {
    const m = await mount((s) => raise(s, 1));
    await openHr();
    await until(() => $('hr-1'), 'the card');
    await press($('hr-settings-1'));
    await until(() => $('hr-settings'), 'the sheet');
    await fill('hs-context_k', 'many');
    await press($('hs-save'));
    expect($('hs-error')?.textContent).toContain('hand off at');
    await fill('hs-context_k', '300');
    await press($('hs-skip_permissions-on'));
    await press($('hs-save'));
    expect($('hs-danger')).not.toBeNull();
    expect(m.daemon.sessions.get(ARCH)!.settings.skip_permissions).toBe('off');
    await press($('hs-save'));
    await until(() => m.daemon.sessions.get(ARCH)!.settings.skip_permissions === 'on', 'the daemon');
    m.store.stop();
  });

  it('says this daemon does not serve HR proposals or the team when they are not listed', async () => {
    const m = await mount((s) => {
      Object.defineProperty(s.daemon, 'served', { get: () => [{ lists: (n: string) => !n.startsWith('hr.') && n !== 'team.get' }] });
    });
    await press($('team-open'));
    await until(() => $('not-served'), 'the sentence');
    expect($('not-served')?.textContent).toBe('This daemon does not serve the team.');
    await press($('team-tab-hr'));
    await until(() => $('not-served')?.textContent === 'This daemon does not serve HR proposals.', 'the HR sentence');
    m.store.stop();
  });
});
