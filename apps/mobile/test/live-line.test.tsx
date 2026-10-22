import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, mount, press, until, pickTab } from '#test/harness.tsx';

const AUD = 'handline.auditor'; // the fake daemon plays it as a structured session

describe('the live work line', () => {
  it("counts this turn's commands, shows the newest, opens them on a tap and folds to a count after the turn", async () => {
    const m = await mount();
    m.daemon.chat.seedItem(AUD, { kind: 'tool', tool: 'Bash', text: 'git status' });
    m.daemon.chat.seedItem(AUD, { kind: 'tool', tool: 'Read', text: 'a.ts' });
    await until(() => $(`agent-${AUD}`), 'the rows');
    await press($(`agent-${AUD}`));
    await pickTab('chat');
    await until(() => $('chat-input'), 'the chat');
    m.daemon.sessions.get(AUD)!.state = 'running';
    await act(async () => m.daemon.chat.setTurn(AUD, true));
    await until(() => $('working')?.textContent?.includes('2 commands · now: Read a.ts'), 'the live line');
    expect($('turn-steps')).toBeNull();
    await press($('working'));
    expect($('turn-steps')!.textContent).toContain('Bash git status');
    await act(async () => m.daemon.chat.setTurn(AUD, false));
    await until(() => $('ran')?.textContent?.includes('Ran 2 commands'), 'the folded line');
    m.store.stop();
  });
});
