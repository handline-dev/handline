import type { AgentDetail } from '@handline/client';
import { controlsOf, holderLabel } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { useWrite } from '@handline/app-data';

/** The terminal tab's strip: who may type now, Take control and Release. Both renderers use it. */
export function TermBar({ agent, detail, onTake }: { agent: string; detail: AgentDetail; onTake?: () => void }) {
  const store = useStore();
  const c = controlsOf(detail, store.me);
  const take = useWrite('input.take', agent);
  const release = useWrite('input.release', agent);
  return (
    <div className="term-bar">
      <span className="holder" title="Who may type now">
        input: {holderLabel(detail.input_holders?.[0], store.me)}
      </span>
      {c.take ? (
        <button
          className="btn"
          disabled={take.isPending}
          onClick={() => (onTake ? onTake() : void take.mutateAsync({ agent }).catch(() => {}))}
        >
          {take.isPending ? 'taking…' : 'Take control'}
        </button>
      ) : null}
      {c.release ? (
        <button className="btn" disabled={release.isPending} onClick={() => void release.mutateAsync({ agent }).catch(() => {})}>
          {release.isPending ? 'releasing…' : 'Release'}
        </button>
      ) : null}
    </div>
  );
}

const CHIPS: [string, string][] = [
  ['esc', 'Esc'],
  ['ctrl-c', '^C'],
  ['enter', '↵'],
  ['up', '↑'],
  ['down', '↓'],
  ['tab', 'Tab'],
  ['shift-tab', '⇧Tab'],
  ['1', '1'],
  ['2', '2'],
  ['3', '3'],
];

/** The key chips under the terminal. What a press does is the tab's: typing and a chip take the same path. */
export function Chips({ enabled, onPress }: { enabled: boolean; onPress: (key: string) => void }) {
  return (
    <div className="chips">
      {CHIPS.map(([key, label]) => (
        <button key={key} className="chip" disabled={!enabled} title={`Send ${label}`} onClick={() => onPress(key)}>
          {label}
        </button>
      ))}
    </div>
  );
}
