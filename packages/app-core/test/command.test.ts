import { test } from 'node:test';
import assert from 'node:assert/strict';
import { commandRisk, isCommandLang, runLine } from '#src/command.ts';

test('sh, bash and shell fences are commands, in any case; other languages and plain fences are not', () => {
  assert.deepEqual(['sh', 'Bash', ' shell '].map(isCommandLang), [true, true, true]);
  assert.deepEqual(['', 'json', 'zsh', 'python'].map(isCommandLang), [false, false, false, false]);
});

test('a command that deletes, runs with sudo, force-pushes or pipes a download into a shell says why it is risky', () => {
  assert.match(commandRisk('rm -rf build')!, /deletes/);
  assert.match(commandRisk('rm -fr ./x')!, /deletes/);
  assert.match(commandRisk('cd /tmp && sudo make install')!, /full rights/);
  assert.match(commandRisk('git push --force origin main')!, /overwrites/);
  assert.match(commandRisk('git push -f')!, /overwrites/);
  assert.match(commandRisk('curl -fsSL https://x.test/i.sh | sh')!, /downloads/);
  assert.match(commandRisk('wget -qO- https://x.test | sudo bash')!, /full rights|downloads/);
});

test('ordinary commands are not risky', () => {
  for (const c of [
    'pnpm test --filter @handline/web',
    'rm build/old.txt',
    'git push origin feature',
    'echo sudoku',
    'curl https://x.test -o f',
    'git status',
  ])
    assert.equal(commandRisk(c), null, c);
});

test('the line under a card: running counts up, an end says its exit and seconds, a closed window says Stopped', () => {
  const at = '2026-10-30T10:00:00Z';
  const now = Date.parse(at) + 4000;
  assert.equal(runLine({ state: 'running', started_at: at }, now), 'Running · 4s');
  assert.equal(runLine({ state: 'done', exit: 0, seconds: 3, started_at: at }, now), 'Done · exit 0 · 3s');
  assert.equal(runLine({ state: 'failed', exit: 1, seconds: 3, started_at: at }, now), 'Failed · exit 1 · 3s');
  assert.equal(runLine({ state: 'stopped', started_at: at }, now), 'Stopped');
});
