import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { NOT_FROM_UI, NOT_QUERIES, PAIRING, reads, writes, keyOf } from '../src/index.ts';

const repo = path.resolve(import.meta.dirname, '../../..');
const protocol = JSON.parse(fs.readFileSync(path.join(repo, 'contract/protocol.json'), 'utf8')) as {
  methods: { name: string; 'x-write': boolean }[];
};
const methods = (write: boolean) => protocol.methods.filter((m) => m['x-write'] === write).map((m) => m.name);

describe('guard 2: every write is classified', () => {
  it('each write method of the contract is in the writes table, or listed as not from the UI, or is the pairing write', () => {
    const known = new Set<string>([...Object.keys(writes), ...NOT_FROM_UI, ...PAIRING]);
    expect(methods(true).filter((m) => !known.has(m))).toEqual([]);
  });
  it('and the lists name no method the contract does not have, and no method twice', () => {
    const all = [...Object.keys(writes), ...NOT_FROM_UI, ...PAIRING];
    expect(all.filter((m) => !methods(true).includes(m))).toEqual([]);
    expect(all.filter((m, i) => all.indexOf(m) !== i)).toEqual([]);
  });
  it('an authored write edits the cache and says which entries; a decided or uncached one edits nothing', () => {
    for (const [method, spec] of Object.entries(writes)) {
      if (spec.kind === 'authored') expect(spec.touches && spec.patch, `${method} needs touches and patch`).toBeTruthy();
      else expect(spec.patch, `${method} must not edit the cache`).toBeUndefined();
    }
  });
});

describe('guard 3: every read has a key', () => {
  it('each read method of the contract is in the reads table or is not a query', () => {
    const known = new Set<string>([...Object.keys(reads), ...NOT_QUERIES]);
    expect(methods(false).filter((m) => !known.has(m))).toEqual([]);
    expect([...Object.keys(reads), ...NOT_QUERIES].filter((m) => !methods(false).includes(m))).toEqual([]);
  });
  it('no two reads share a resource name unless their params tell them apart', () => {
    const names = Object.values(reads).map((r) => r.resource);
    expect(names.filter((n, i) => names.indexOf(n) !== i)).toEqual([]);
  });
  it('every key starts with the daemon, so two daemons never share an entry', () => {
    expect(keyOf('a', 'agent.get', { agent: 'x' })).toEqual(['d', 'a', 'agent', 'x']);
    expect(keyOf('b', 'agents.list', {})).toEqual(['d', 'b', 'agents']);
    expect(keyOf('a', 'tasks.list', { state: 'doing' })).toEqual(['d', 'a', 'tasks', { state: 'doing' }]);
    expect(keyOf('a', 'agent.triggers', { agent: undefined })).toEqual(['d', 'a', 'triggers']);
  });
});

/** Files that still call the daemon by hand. Q2 and Q3 move them; this list may only get shorter. */
const STILL_BY_HAND = ['apps/mobile/src/screens/AgentPage.tsx', 'apps/web/src/TerminalTab.tsx'];
const BY_HAND =
  /\bclient\.call\(|\.act\(|\bclient\.(startSession|restartSession|parkSession|stopSession|sendInput|sendKeys|interrupt|answerPrompt|takeInput|releaseInput|openPrompts)\(|\bstore\.(startAgent|resume|stopAgent|send|key|interrupt|answer|take|release)\(/;

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return ['node_modules', 'dist', 'test', 'ios', 'android', '.expo'].includes(e.name) ? [] : sources(p);
    return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
  });
}

describe('guard 1: no hand-made data calls in an app', () => {
  const offenders = sources(path.join(repo, 'apps'))
    .filter((f) => BY_HAND.test(fs.readFileSync(f, 'utf8')))
    .map((f) => path.relative(repo, f));
  it('only the files not yet moved to app-data call the daemon or write through the store', () => {
    expect(offenders.filter((f) => !STILL_BY_HAND.includes(f))).toEqual([]);
  });
  it('and a file that no longer does is taken off the list', () => {
    expect(STILL_BY_HAND.filter((f) => !offenders.includes(f))).toEqual([]);
  });
});
