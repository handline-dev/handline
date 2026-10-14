import { describe, expect, it, vi } from 'vitest';
import { RpcError } from '@handline/client';
import { $, mount, press, until } from './harness.tsx';
import type { setup } from '@handline/fake-daemon/helpers';

const AUD = 'handline.auditor'; // the fake daemon plays it as a structured session
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

/** A message from the person with one image per name, read back from the fake's own store; returns the image ids. */
function seedPictures(s: ReturnType<typeof setup>, body: string, names: string[]) {
  const ids = names.map((name) => {
    const r = s.daemon.uploads.put({ name, mime: 'image/png', bytes: 70, data: PNG });
    return r.attachment!.id;
  });
  s.daemon.chat.send({ agent: AUD, body, request_id: `pic-${body}`, attachments: ids }, 'founder', s.daemon.uploads.take);
  return ids;
}

const openChat = async () => {
  await until(() => $(`agent-${AUD}`), 'the rows');
  await press($(`agent-${AUD}`));
  await until(() => $('tab-chat'), 'the page');
  await press($('tab-chat'));
};
/** The Image inside a thumbnail: react-native-web draws it as a div carrying the accessibilityLabel (the name); the button holds "Open <name>". */
const image = (id: string) => $(`picture-${id}`)?.querySelector('div[aria-label]') ?? null;

describe('the pictures of a message in Chat', () => {
  it('draws each as a thumbnail; a tap opens it full screen and Close shuts it', async () => {
    let ids: string[] = [];
    const m = await mount((s) => void (ids = seedPictures(s, 'look at these', ['a.png', 'b.png'])));
    await openChat();
    await until(() => $('pictures') && image(ids[0]!) && image(ids[1]!), 'both thumbnails');
    expect($('pictures')?.children.length).toBe(2);
    expect($(`picture-full-${ids[0]}`)).toBeNull();
    await press($(`picture-${ids[0]}`));
    await until(() => document.body.querySelector(`[data-testid="picture-full-${ids[0]}"]`), 'the full view');
    await until(() => document.body.querySelector('[data-testid="picture-image"]'), 'the original');
    await press(document.body.querySelector<HTMLElement>('[data-testid="picture-close"]'));
    await until(() => !document.body.querySelector('[data-testid="picture-full-' + ids[0] + '"]'), 'the view closed');
    m.store.stop();
  });

  it('a message of only pictures has no empty body', async () => {
    let ids: string[] = [];
    const m = await mount((s) => void (ids = seedPictures(s, '', ['only.png'])));
    await openChat();
    await until(() => $('pictures') && image(ids[0]!), 'the thumbnail');
    expect(document.body.querySelector('[data-testid^="body-"]')).toBeNull();
    m.store.stop();
  });

  it('says so for an image the daemon no longer keeps', async () => {
    let ids: string[] = [];
    const m = await mount((s) => {
      ids = seedPictures(s, 'gone soon', ['old.png']);
      s.daemon.uploads.expire(ids[0]!);
    });
    await openChat();
    await until(() => $(`picture-${ids[0]}`)?.textContent?.includes('no longer kept'), 'the line');
    expect($(`picture-${ids[0]}`)?.textContent).toContain('old.png');
    m.store.stop();
  });

  it('the full view says so when the original is gone', async () => {
    let ids: string[] = [];
    const m = await mount((s) => void (ids = seedPictures(s, 'kept as a thumbnail', ['t.png'])));
    await openChat();
    await until(() => image(ids[0]!), 'the thumbnail');
    m.daemon.uploads.expire(ids[0]!);
    await press($(`picture-${ids[0]}`));
    await until(() => document.body.querySelector('[data-testid="picture-line"]')?.textContent?.includes('no longer kept'), 'the line');
    expect(document.body.querySelector('[data-testid="picture-image"]')).toBeNull();
    m.store.stop();
  });

  it('says so when the read fails', async () => {
    let ids: string[] = [];
    const m = await mount((s) => void (ids = seedPictures(s, 'cannot read', ['x.png'])));
    const call = m.store.client.call.bind(m.store.client);
    vi.spyOn(m.store.client, 'call').mockImplementation(((method: string, p: never) =>
      method === 'attachment.get' ? Promise.reject(new RpcError('transport', 'lost')) : call(method as never, p)) as never);
    await openChat();
    await until(() => $(`picture-${ids[0]}`)?.textContent?.includes('could not be loaded'), 'the line');
    m.store.stop();
  });
});
