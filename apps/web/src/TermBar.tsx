import type { AgentDetail } from '@handline/client';
import { controlsOf, holderLabel } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { useWrite } from '@handline/app-data';

/** The terminal tab's strip: who holds the input, as an icon that also hands it over or back. Typing takes the input by itself and gives it back when it stops. */
export function TermBar({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const store = useStore();
  const c = controlsOf(detail, store.me);
  const take = useWrite('input.take', agent);
  const release = useWrite('input.release', agent);
  const holder = holderLabel(detail.input_holders?.[0], store.me);
  const mine = holder === 'YOU';
  const daemon = holder === 'daemon';
  const mark = mine ? '⌨' : daemon ? '⚙' : '⚿';
  const say = mine
    ? 'You hold the input. It goes back to the daemon after a quiet spell; click to give it back now.'
    : daemon
      ? 'The daemon holds the input. Typing here takes it; click to take it now.'
      : `${holder} holds the input.`;
  const act = () => void (c.release ? release : take).mutateAsync({ agent }).catch(() => {});
  return (
    <div className="term-bar">
      <button
        className={`holder${mine ? ' mine' : ''}`}
        title={say}
        aria-label={`Input: ${holder}. ${say}`}
        disabled={!(c.take || c.release) || take.isPending || release.isPending}
        onClick={act}
      >
        <span aria-hidden>{mark}</span> {mine ? 'you' : holder}
      </button>
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
