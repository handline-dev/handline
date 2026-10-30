// Command cards in a chat message: a sh/bash/shell fence has Copy and Run; Run asks first, runs through the daemon and the line under the card says how it went.
import { describe, expect, it } from 'vitest';
import { act } from 'react';
import { copied } from '#test/clipboard-stub.ts';
import { $, mount, press, until, pickTab, sleep } from '#test/harness.tsx';
import type { setup } from '@handline/fake-daemon/helpers';

const AUD = 'handline.auditor';
const $m = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);

async function open(body: string) {
  const m = await mount(
    (s: ReturnType<typeof setup>) => void s.daemon.chat.send({ agent: AUD, body, request_id: 'cmd' }, 'founder', s.daemon.uploads.message),
  );
  await until(() => $(`agent-${AUD}`), 'the rows');
  await press($(`agent-${AUD}`));
  await pickTab('chat');
  await until(() => $m('command-0'), 'the card');
  return m;
}

describe('command cards', () => {
  it('a sh fence is a card with Copy and Run; another fence stays plain code', async () => {
    const m = await open('Run this:\n```sh\npnpm test\n```\nand see\n```json\n{"a":1}\n```');
    expect($m('command-0')?.textContent).toContain('pnpm test');
    expect($m('command-copy-0')).not.toBeNull();
    expect($m('command-run-0')).not.toBeNull();
    expect($m('command-1')).toBeNull();
    expect(document.body.textContent).toContain('{"a":1}');
    m.store.stop();
  });

  it('Copy puts the command on the clipboard and says Copied for a moment', async () => {
    const m = await open('```bash\necho hi\n```');
    await press($m('command-copy-0'));
    await until(() => $m('command-copied-0'), 'the Copied label');
    expect(copied.at(-1)).toBe('echo hi');
    await act(async () => void (await new Promise((r) => setTimeout(r, 1700))));
    expect($m('command-copied-0')).toBeNull();
    m.store.stop();
  });

  it('Run asks first; Cancel runs nothing; Run starts it and the line says Done · exit 0', async () => {
    const m = await open('```sh\ntrue\n```');
    await press($m('command-run-0'));
    await until(() => $m('command-confirm-0'), 'the question');
    expect($m('command-confirm-0')?.textContent).toContain('It runs on your computer as you.');
    expect($m('command-risk-0')).toBeNull();
    await press($m('command-cancel-0'));
    expect($m('command-result-0')).toBeNull();
    await press($m('command-run-0'));
    await press($m('command-go-0'));
    await until(() => $m('command-result-0'), 'the result line');
    expect($m('command-result-0')?.textContent).toContain('Done · exit 0 · 0s');
    m.store.stop();
  });

  it('a command that fails says Failed with its exit', async () => {
    const m = await open('```sh\nfalse\n```');
    await press($m('command-run-0'));
    await press($m('command-go-0'));
    await until(() => $m('command-result-0')?.textContent?.includes('Failed · exit 1'), 'the failed line');
    m.store.stop();
  });

  it('a risky command says why in the question', async () => {
    const m = await open('```sh\nrm -rf build\n```');
    await press($m('command-run-0'));
    await until(() => $m('command-risk-0'), 'the reason');
    expect($m('command-risk-0')?.textContent).toContain('deletes');
    m.store.stop();
  });

  it('the second command of a message runs as its own block', async () => {
    const m = await open('```sh\ntrue\n```\nthen\n```sh\nfalse\n```');
    await press($m('command-run-1'));
    await press($m('command-go-1'));
    await until(() => $m('command-result-1')?.textContent?.includes('Failed'), 'the second result');
    expect($m('command-result-0')).toBeNull();
    await sleep(0);
    m.store.stop();
  });
});
