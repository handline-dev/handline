import { act } from 'react';
import { Alert } from 'react-native';
import { describe, expect, it, vi } from 'vitest';
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

describe('the controls of a structured agent', () => {
  it('shows Interrupt beside the working line only while a turn runs', async () => {
    const m = await mount();
    await open(m);
    await until(() => $('transcript'), 'the transcript');
    expect($('tr-interrupt')).toBeNull();
    await act(async () => {
      m.daemon.transcript.push(AUD, { kind: 'turn_start', turn: 'live', turn_start: { message: 'live', from: 'founder', text: 'go' } });
      m.daemon.sessions.get(AUD)!.state = 'running';
    });
    await until(() => $('tr-interrupt'), 'the Interrupt button');
    await press($('tr-interrupt'));
    await until(() => m.daemon.sessions.get(AUD)!.state === 'idle', 'the daemon interrupted the turn');
    m.store.stop();
  });

  it('never renders a terminal for a structured agent', async () => {
    const m = await mount();
    await open(m);
    await until(() => $('transcript'), 'the transcript');
    expect($('terminal')).toBeNull();
    expect($('take')).toBeNull();
    expect($('composer')).toBeNull();
    m.store.stop();
  });
});

describe('a structured agent’s prompt and its stored session', () => {
  it('reaches the Chat tab as Yes and No from detail.prompt', async () => {
    const m = await mount((s) => {
      s.daemon.sessions.get(AUD)!.prompt = {
        id: 'p9',
        agent: AUD,
        kind: 'permission',
        tool: 'Bash',
        command: 'git push',
        reason: null,
        lines: [],
        since: '2026-10-09T00:00:00Z',
        options: [
          { key: '1', label: 'Yes', answer: 'yes' },
          { key: '2', label: 'No', answer: 'no' },
        ],
      };
    });
    await until(() => $(`agent-${AUD}`), 'the rows');
    await press($(`agent-${AUD}`));
    await until(() => $('prompt-card'), 'the card on the Chat tab');
    expect($('terminal')).toBeNull();
    expect([...$('prompt-card')!.querySelectorAll<HTMLElement>('[data-testid^="answer-"]')].map((b) => b.textContent)).toEqual([
      'Yes',
      'No',
    ]);
    m.store.stop();
  });
});

const buttons = () => [...document.querySelectorAll<HTMLElement>('[role="button"]')].map((b) => b.textContent);
const button = (label: string) =>
  [...document.querySelectorAll<HTMLElement>('[role="button"]')].find((b) => b.textContent === label) ?? null;

describe('the header row of a structured agent', () => {
  it('lists Close and Kill while idle, with no Exit and no Interrupt', async () => {
    const m = await mount();
    await until(() => $(`agent-${AUD}`), 'the rows');
    await press($(`agent-${AUD}`));
    await until(() => button('Close'), 'the header row');
    expect(buttons()).toContain('Kill');
    expect(buttons()).not.toContain('Exit');
    expect(buttons()).not.toContain('Interrupt');
    m.store.stop();
  });

  it('adds Interrupt while a turn runs', async () => {
    const m = await mount((s) => {
      s.daemon.sessions.get(AUD)!.state = 'running';
      s.daemon.chat.setTurn(AUD, true);
    });
    await until(() => $(`agent-${AUD}`), 'the rows');
    await press($(`agent-${AUD}`));
    await until(() => button('Interrupt'), 'Interrupt');
    expect(buttons()).not.toContain('Exit');
    m.store.stop();
  });

  it('offers Resume and Start new for a closed one; Resume wakes it and Start new asks first', async () => {
    const m = await mount();
    await until(() => $(`agent-${AUD}`), 'the rows');
    await act(async () => void (await m.store.client.stopSession(AUD, 'close')));
    await press($(`agent-${AUD}`));
    await until(() => button('Resume'), 'Resume');
    const alert = vi.spyOn(Alert, 'alert');
    await press(button('Start new'));
    expect(alert).toHaveBeenCalledWith(expect.stringContaining('over?'), expect.anything(), expect.anything());
    expect(m.daemon.sessions.get(AUD)!.open).toBe(false);
    alert.mockRestore();
    await press(button('Resume'));
    await until(() => m.daemon.sessions.get(AUD)!.open, 'the session woken');
    m.store.stop();
  });
});

describe('a refused transcript read of a structured agent', () => {
  it('is an error with Retry, not "not served"', async () => {
    const m = await mount((s) => s.daemon.knobs.refuse('transcript.events', 'unsupported', 'no transcript'));
    await open(m);
    await until(() => $('error'), 'the error block');
    expect($('not-served')).toBeNull();
    expect($('terminal')).toBeNull();
    m.daemon.knobs.clear();
    await press(button('Retry'));
    await until(() => $('transcript'), 'the turns after Retry');
    m.store.stop();
  });
});
