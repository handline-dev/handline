import { describe, it } from 'vitest';
import { ARCH, $, fill, mount, pickTab, press, text, until } from '#test/harness.tsx';

async function openTab(tab: string) {
  const m = await mount(() => {}, undefined, 'founder');
  await until(() => $(`agent-${ARCH}`), 'the rows');
  await press($(`agent-${ARCH}`));
  await pickTab(tab);
  return m;
}

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
