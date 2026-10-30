import { describe, expect, it } from 'vitest';
import { riskOf, isShell } from '#src/CommandCard.tsx';
import { parseBlocks } from '#src/Markdown.tsx';

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
