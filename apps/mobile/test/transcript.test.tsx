import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, mount, press, text, until } from './harness.tsx';

const AUD = 'handline.auditor'; // the fake daemon plays it as a structured session
const open = async (m: Awaited<ReturnType<typeof mount>>, agent = AUD) => {
  await until(() => $(`agent-${agent}`), 'the rows');
  await press($(`agent-${agent}`));
  await until(() => $('tab-terminal'), 'the page');
  await press($('tab-terminal'));
};

describe('the transcript of a structured agent', () => {
  it('draws every event kind of the fixture in place of the terminal', async () => {
    const m = await mount();
    await open(m);
    await until(() => $('transcript'), 'the transcript');
    expect($('terminal')).toBeNull();
    expect($('tab-terminal')?.textContent).toBe('Transcript');
    const t = text();
    expect(t).toContain('founder: Run the contract tests.'); // turn_start
    expect(t).toContain('Running them now.'); // text
    expect($('tr-3')?.textContent).toContain('Bash python3 tests/infra/test_contract.py'); // tool_use
    expect($('tr-3')?.textContent).not.toContain('"command"'); // collapsed
    await press($('tr-3'));
    expect($('tr-3')?.textContent).toContain('"command"');
    expect(t).toContain('Allowed by person'); // permission_answer
    expect($('result-toolu_a')?.textContent).toContain('Bash returned: 26 passed, 0 failed'); // tool_result
    expect(t).toContain('rate_limited: The API asked to retry'); // error
    expect(t).toContain('done · $0.04 · 8.2 s'); // turn_end
    m.store.stop();
  });

  it('follows the stream and answers a permission card with prompt.answer', async () => {
    const m = await mount();
    await open(m);
    await until(() => $('transcript'), 'the transcript');
    await act(async () => {
      m.daemon.transcript.push(AUD, { kind: 'turn_start', turn: 'live', turn_start: { message: 'live', from: 'founder', text: 'push' } });
      m.daemon.transcript.ask(AUD, 'live', 'Bash', 'git push');
    });
    await until(() => $('prompt-card'), 'the card in the transcript');
    expect($('transcript')?.contains($('prompt-card'))).toBe(true);
    await press($('answer-allow'));
    await until(() => $('prompt-card') === null && $('turn-9')?.textContent?.includes('Allowed by person'), 'the answer in the stream');
    expect($('turn-9')?.textContent).toContain('Bash returned: done');
    m.store.stop();
  });

  it('keeps the terminal for a terminal agent', async () => {
    const m = await mount();
    await open(m, 'handline.architect');
    await until(() => $('terminal'), 'the terminal');
    expect($('transcript')).toBeNull();
    expect($('tab-terminal')?.textContent).toBe('Terminal');
    m.store.stop();
  });
});

describe('the transcript tab states', () => {
  it('shows the transcript skeleton in the tab while the read is slow, with no terminal, then the turns', async () => {
    const m = await mount();
    await until(() => $(`agent-${AUD}`), 'the rows');
    m.daemon.knobs.latency(600);
    await press($(`agent-${AUD}`));
    await until(() => $('tab-terminal'), 'the page', 3000);
    await press($('tab-terminal'));
    await until(() => $('skeleton-terminal'), 'the skeleton', 1500);
    expect($('terminal')).toBeNull();
    expect($('tab-terminal')?.textContent).toBe('Transcript'); // the daemon said so before any read
    await until(() => $('transcript'), 'the turns', 5000);
    expect($('skeleton-terminal')).toBeNull();
    expect($('tab-terminal')?.textContent).toBe('Transcript');
    m.store.stop();
  });

  it('shows the daemon’s sentence when the read is refused, and Retry reads again', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('transcript.events', 'state', 'the daemon is resting'));
    await open(m);
    await until(() => $('error'), 'the error block');
    expect(text()).toContain('the daemon is resting');
    expect($('terminal')).toBeNull();
    m.daemon.knobs.clear();
    await press([...document.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === 'Retry') ?? null);
    await until(() => $('transcript'), 'the turns after Retry');
    expect($('error')).toBeNull();
    m.store.stop();
  });

  it('fills in what happened while the connection was down', async () => {
    const m = await mount();
    await open(m);
    await until(() => $('transcript'), 'the turns');
    m.daemon.dropConnections();
    await act(async () => {
      m.daemon.transcript.push(AUD, { kind: 'text', turn: 'late', text: 'written while you were away' });
    });
    await until(() => text().includes('written while you were away'), 'the missed event', 5000);
    m.store.stop();
  });
});

describe('the transcript as a member', () => {
  it('shows a structured agent’s transcript and answers its card on a paired phone', async () => {
    const m = await mount(undefined, undefined, undefined, { role: 'member' });
    await open(m);
    await until(() => $('transcript'), 'the transcript');
    await act(async () => {
      m.daemon.transcript.push(AUD, { kind: 'turn_start', turn: 'live', turn_start: { message: 'live', from: 'founder', text: 'push' } });
      m.daemon.transcript.ask(AUD, 'live', 'Bash', 'git push');
    });
    await until(() => $('prompt-card'), 'the card');
    await press($('answer-allow'));
    await until(() => $('prompt-card') === null && text().includes('Allowed by person'), 'the answer');
    m.store.stop();
  });
});
