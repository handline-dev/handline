// Sealing the team files from the phone's Team page, on the fake daemon: the dry run, Seal pending and then the daemon's line, a refusal, and no control for a member or without team.seal.
import { describe, expect, it } from 'vitest';
import { $, box, mount, press, until } from '#test/harness.tsx';

const text = (el: Element | null = box) => el?.textContent ?? '';
async function team(prepare?: Parameters<typeof mount>[0], role?: 'member') {
  const m = await mount(prepare, undefined, undefined, { role });
  await until(() => $('agents-menu'), 'the entry');
  await press($('agents-menu'));
  await press($('team-open'));
  await until(() => $('team-list'), 'the team');
  return m;
}

describe('seal the team files', () => {
  it('lists what a dry run would seal, then seals and shows the daemon’s line', async () => {
    const m = await team();
    await press($('seal-open'));
    await until(() => $('seal-plan'), 'the plan');
    expect(text($('seal-plan'))).toContain('team.yaml: changed');
    expect(text($('seal-plan'))).toContain('handline.new-builder/agent.yaml: new');
    m.daemon.knobs.latency(300);
    const sealing = press($('seal-go'));
    await until(() => text($('seal-go')).includes('Sealing…'), 'Seal pending');
    await sealing;
    await until(() => $('seal-said'), 'the line');
    expect(text($('seal-said'))).toBe('sealed 2 files');
    expect($('seal-go')).toBeNull();
    await press($('seal-cancel'));
    expect($('seal-sheet')).toBeNull();
    await press($('seal-open'));
    await until(() => $('seal-plan'), 'the plan');
    expect(text($('seal-plan'))).toContain('nothing needs sealing');
    m.store.stop();
  });

  it('says why when the daemon refuses', async () => {
    const m = await team((s) => s.daemon.knobs.refuse('team.seal', 'state', 'the files are being written'));
    await press($('seal-open'));
    await until(() => $('seal-error'), 'the refusal');
    expect(text($('seal-error'))).toContain('the files are being written');
    expect(($('seal-go') as unknown as HTMLButtonElement).disabled).toBe(true);
    m.store.stop();
  });

  it('shows no control to a member', async () => {
    const a = await team(undefined, 'member');
    expect($('seal-open')).toBeNull();
    a.store.stop();
  });

  it('shows no control on a daemon that does not serve team.seal', async () => {
    const m = await team((s) => s.daemon.knobs.unserve('team.seal'));
    expect($('seal-open')).toBeNull();
    m.store.stop();
  });
});
