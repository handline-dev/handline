// The phone's Requests page (T5928 M1): the founder's requests with their status, a filter, a tap that opens the task, and the line under his message
// that says what the team made of it.
import { describe, expect, it } from 'vitest';
import { $, ARCH, mount, press, text, until } from '#test/harness.tsx';

async function open(m: Awaited<ReturnType<typeof mount>>) {
  await until(() => $('agents-menu'), 'the Agents menu');
  await press($('agents-menu'));
  await until(() => $('requests-open'), 'the My requests entry');
  await press($('requests-open'));
  await until(() => $('requests-back'), 'the page');
  expect(text()).toContain('My requests');
  return m;
}

describe('the phone Requests page', () => {
  it('lists the requests with status, holders and counts, and filters by status', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    m.daemon.requests.add({
      title: 'Show the usage first',
      task: 6071,
      status: 'doing',
      holders: ['handline.pc'],
      open: 2,
      done: 1,
      prs: [628],
    });
    m.daemon.requests.add({ title: 'Which build is live?', task: 6020, status: 'waiting_on_you', agent: 'handline', unread_comments: 2 });
    await open(m);
    await until(() => $('request-6020') && $('request-6071'), 'both requests');
    expect(text($('request-6020'))).toContain('Needs you');
    expect(text($('request-6020'))).toContain('2 new comments');
    expect(text($('request-6071'))).toContain('2 open · 1 done');
    expect(text($('request-6071'))).toContain('#628');
    await press($('requests-filter-doing'));
    expect($('request-6020')).toBeNull();
    expect($('request-6071')).not.toBeNull();
    m.store.stop();
  });

  it('opens the task of a request in the Tasks tab, and the back button returns to the list', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    m.daemon.requests.add({ title: 'Show the usage first', task: 1684, status: 'doing' });
    await open(m);
    await until(() => $('request-1684'), 'the request');
    await press($('requests-back'));
    await until(() => $('agents-menu'), 'the list');
    await open(m);
    await press($('request-1684'));
    await until(() => $('task-detail'), 'the task');
    expect($('requests-back')).toBeNull();
    m.store.stop();
  });

  it('says under the founder’s message which task it became, or why it needs none', async () => {
    const m = await mount(() => {}, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    const all = (m.daemon.chat as unknown as { messages: { id: number; from: string; agent: string }[] }).messages;
    const ids = all.filter((x) => x.agent === ARCH && x.from !== ARCH).map((x) => x.id);
    expect(ids.length).toBeGreaterThan(1);
    m.daemon.chat.change(m.daemon.chat.find(ids[0]) as never, { request_task: 1684 } as never);
    m.daemon.chat.change(m.daemon.chat.find(ids[1]) as never, { no_work: 'just thanks' } as never);
    await press($(`agent-${ARCH}`));
    await until(() => $('chat-input'), 'the chat');
    await until(() => $(`request-chip-${ids[0]}`), 'the chip');
    expect(text($(`request-chip-${ids[0]}`))).toBe('Taken as T1684');
    expect(text($(`request-chip-${ids[1]}`))).toBe('Nothing to do: just thanks');
    m.store.stop();
  });
});
