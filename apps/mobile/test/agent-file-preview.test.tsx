import { describe, expect, it } from 'vitest';
import { addressOf } from '../src/screens/AgentPreview.tsx';
import { ARCH, $, fill, mount, press, text, until } from './harness.tsx';

async function openTab(tab: string) {
  const m = await mount(() => {}, undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await until(() => $(`tab-${tab}`), 'the tab');
  await press($(`tab-${tab}`));
  return m;
}

describe('addressOf', () => {
  it('adds http to a bare host and port', () => expect(addressOf(' my-mac.ts.net:5173 ')).toBe('http://my-mac.ts.net:5173'));
  it('keeps a full address and refuses an empty one', () => {
    expect(addressOf('https://a.dev/x')).toBe('https://a.dev/x');
    expect(addressOf('  ')).toBeNull();
  });
});

describe('the File tab of the agent page', () => {
  it('opens a markdown file, edits it and saves', async () => {
    await openTab('file');
    await fill('file-path', 'docs/pc-app-gaps.md');
    await press($('file-open'));
    await until(() => text().includes('the File tab'), 'the file');
    await press($('file-edit'));
    await until(() => $('file-text'), 'the editor');
    await fill('file-text', '# PC app gaps\n\n- edited on the phone\n');
    await press($('file-save'));
    await until(() => text().includes('edited on the phone') && !$('file-text'), 'the saved text');
  });
});

describe('the Preview tab of the agent page', () => {
  it('shows the address typed in a web view', async () => {
    await openTab('preview');
    await fill('preview-address', 'my-mac.ts.net:5173');
    await press($('preview-go'));
    await until(() => $('preview-web'), 'the web view');
    expect($('preview-web')!.getAttribute('data-uri')).toBe('http://my-mac.ts.net:5173');
  });
});
