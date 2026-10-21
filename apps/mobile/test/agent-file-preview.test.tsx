import { describe, expect, it } from 'vitest';
import { addressOf } from '#src/screens/AgentPreview.tsx';
import { ARCH, $, fill, mount, pickTab, press, text, until } from '#test/harness.tsx';

async function openTab(tab: string) {
  const m = await mount(() => {}, undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await pickTab(tab);
  return m;
}

describe('addressOf', () => {
  it('adds https to a bare host', () => expect(addressOf('example.com')).toBe('https://example.com'));
  it('adds http to a bare tailnet host and port', () => expect(addressOf(' my-mac.ts.net:5173 ')).toBe('http://my-mac.ts.net:5173'));
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

describe('Mark an area and Open outside on the Preview tab', () => {
  it('opens the address outside the app, and the picture of the page opens to be marked', async () => {
    const opened: string[] = [];
    const real = window.open;
    window.open = ((u: string) => void opened.push(u)) as typeof window.open;
    try {
      await openTab('preview');
      await fill('preview-address', 'my-mac.ts.net:5173');
      await press($('preview-go'));
      await until(() => $('preview-outside'), 'the buttons');
      await press($('preview-outside'));
      expect(opened[0]).toContain('http://my-mac.ts.net:5173');
      await press($('preview-mark'));
      await until(() => $('mark-done'), 'the marks editor');
      await press($('mark-cancel'));
      await until(() => !$('mark-done'), 'the editor to close');
    } finally {
      window.open = real;
    }
  });
});
