import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { taskRef } from '@handline/app-core';

const files = (dir: string): string[] =>
  readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(n) ? [p] : [];
  });

// A task id drawn after `#`: the `#{x.id}` / `#${x.id}` of a task-shaped value, or the words "task #". A message is #N; a task is T<N>.
const BAD = /#\$?\{(?:t|b|task|d\.task|a\.task|props)\.(?:id|parent|task)\b|#\$?\{(?:cursor|props\.id)\}|[Tt]asks? #|#\$?\{t\.id < 0/;

describe('task numbers', () => {
  it('are T<N>', () => expect(taskRef(3515)).toBe('T3515')); // local-ref-ok: the format under test
  it('are never drawn with # in the web or phone screens', () => {
    const roots = [join(__dirname, '../src'), join(__dirname, '../../mobile/src')];
    const hits = roots.flatMap(files).flatMap((f) =>
      readFileSync(f, 'utf8')
        .split('\n')
        .flatMap((line, i) => (BAD.test(line) ? [`${f}:${i + 1}: ${line.trim()}`] : [])),
    );
    expect(hits).toEqual([]);
  });
});
