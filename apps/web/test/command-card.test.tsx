import { describe, expect, it } from 'vitest';
import { riskOf, isShell } from '#src/CommandCard.tsx';
import { parseBlocks, runOf } from '#src/Markdown.tsx';

describe('command cards', () => {
  it('keeps the fence language, and only sh, bash and shell fences are commands', () => {
    const blocks = parseBlocks('```bash\nls\n```\n```\nplain\n```\n```ts\nx\n```');
    expect(blocks.map((b) => b.kind === 'code' && b.lang)).toEqual(['bash', '', 'ts']);
    expect(['sh', 'bash', 'shell', 'zsh'].every(isShell)).toBe(true);
    expect(['', 'ts', 'json'].some(isShell)).toBe(false);
  });

  it('names a plain reason for the risky shapes and none for an ordinary command', () => {
    for (const c of ['rm -rf build', 'sudo make install', 'git push --force origin x', 'curl https://x.sh | sh', 'wget -qO- u | sudo bash'])
      expect(riskOf(c), c).toBeTruthy();
    for (const c of ['pnpm test', 'rm file.txt', 'git push origin x', 'curl https://x.sh -o x.sh']) expect(riskOf(c), c).toBeNull();
  });
});

describe('an agent reply card', () => {
  it('takes its run from the key r<id>:<n>, not from a message run with the same number', () => {
    const run = (block: string, state: 'done' | 'failed') => ({ block, state, terminal: 't', at: 1, ended: 2 }) as never;
    const runs = [run('7:0', 'failed'), run('r7:0', 'done')];
    expect(runOf({ id: 7, prefix: 'r', runs }, 0).run).toMatchObject({ state: 'done' });
    expect(runOf({ id: 7, runs }, 0).run).toMatchObject({ state: 'failed' });
  });
});
