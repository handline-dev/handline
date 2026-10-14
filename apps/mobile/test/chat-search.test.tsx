// Search above the phone's Chat against the fake daemon: hits as rows, a tap lands on the message, an empty result says so.
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { $, ARCH, mount, press, sleep, text, until } from './harness.tsx';

async function open(prepare?: Parameters<typeof mount>[0]) {
  const m = await mount(prepare);
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $('tab-chat'), 'the page');
  await press($('tab-chat'));
  await until(() => $('chat-input'), 'the chat');
  return m;
}
const search = async (value: string) => {
  const input = $('chat-search') as unknown as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await sleep(300); // the box waits for the typing to stop
};
const hits = () => [...document.body.querySelectorAll<HTMLElement>('[data-testid^="hit-"]')];

describe('chat search on the phone', () => {
  it('lists the messages that hold the text and lands the chat on the one tapped', async () => {
    const m = await open();
    await search('contract');
    await until(() => hits().length, 'a hit');
    expect(hits()).toHaveLength(1);
    expect(text()).toContain('Use the contract fixtures.');
    const id = hits()[0]!.getAttribute('data-testid')!.replace('hit-', '');
    await press(hits()[0]!);
    await until(() => $(`found-${id}`), 'the message marked');
    expect(hits()).toHaveLength(0);
    m.store.stop();
  });

  it('says so when nothing holds the text', async () => {
    const m = await open();
    await search('zzz-nothing-says-this-zzz');
    await until(() => $('search-empty'), 'the empty line');
    expect(text($('search-empty'))).toContain('No messages found');
    m.store.stop();
  });

  it('searches the scope the tab shows: a message between two agents is not in Mine', async () => {
    const m = await open();
    await search('Review is done');
    await until(() => $('search-empty'), 'no hit in Mine');
    await press($('scope-all'));
    await search('');
    await search('Review is done');
    await until(() => hits().length, 'the hit in All');
    expect(hits()[0]!.textContent).toContain('handline.auditor');
    m.store.stop();
  });

  it('reads older pages until a hit beyond the first page is drawn', async () => {
    const m = await open((s) => {
      s.daemon.chat.seed(ARCH, { from: 'founder', to: ARCH, body: 'the needle of long ago' });
      for (let i = 0; i < 60; i++) s.daemon.chat.seed(ARCH, { from: 'founder', to: ARCH, body: `filler ${i}` });
    });
    await search('needle');
    await until(() => hits().length, 'a hit');
    expect($('msg-' + hits()[0]!.getAttribute('data-testid')!.replace('hit-', ''))).toBeNull();
    const id = hits()[0]!.getAttribute('data-testid')!.replace('hit-', '');
    await press(hits()[0]!);
    await until(() => $(`found-${id}`), 'the older message marked');
    m.store.stop();
  });

  it('says so when the daemon cannot find a message that is not drawn', async () => {
    const m = await open((s) => {
      s.daemon.knobs.refuse('chat.locate', 'state', 'gone');
      s.daemon.chat.seed(ARCH, { from: 'founder', to: ARCH, body: 'the needle of long ago' });
      for (let i = 0; i < 60; i++) s.daemon.chat.seed(ARCH, { from: 'founder', to: ARCH, body: `filler ${i}` });
    });
    await search('needle');
    await until(() => hits().length, 'a hit');
    await press(hits()[0]!);
    await until(() => $('search-lost'), 'the notice');
    m.store.stop();
  });

  it('a daemon that does not list chat.search shows no box', async () => {
    const m = await open((s) => s.daemon.knobs.unserve('chat.search'));
    expect($('chat-search')).toBeNull();
    m.store.stop();
  });
});
