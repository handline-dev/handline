// What the live line calls a Bash call.
import { describe, expect, it } from 'vitest';
import { bashName } from '#src/bashName.ts';

describe('bashName', () => {
  it('names the words the command starts with', () => {
    expect(bashName('Bash hl task new --assign x "do it"')).toBe('hl task new');
    expect(bashName('Bash git status')).toBe('git status');
    expect(bashName('Bash ls -la')).toBe('ls');
  });
  it('skips a leading cd and names what runs', () => {
    expect(bashName('Bash cd /Users/ron/work && pnpm test --filter web')).toBe('pnpm test');
    expect(bashName('Bash cd /a; cd b && git diff')).toBe('git diff');
  });
  it('skips an env prefix', () => {
    expect(bashName('Bash FOO=1 BAR=2 pnpm test')).toBe('pnpm test');
  });
  it('names the first command of a pipe', () => {
    expect(bashName('Bash git log --oneline | head -5')).toBe('git log');
    expect(bashName('Bash cat a.ts | grep x')).toBe('cat a.ts');
  });
  it('names a script by its file', () => {
    expect(bashName('Bash ./tools/check.sh')).toBe('check.sh');
  });
  it('gives nothing when nothing runs', () => {
    expect(bashName('Bash cd /a')).toBe('');
    expect(bashName('Bash ')).toBe('');
  });
});
