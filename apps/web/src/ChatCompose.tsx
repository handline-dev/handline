// The message box: Enter sends, Shift+Enter breaks the line, the up arrow recalls what was sent, and an unsent draft is kept per agent.
import { useRef, useState } from 'react';
import type { KV } from '@handline/app-core';
import { useChatActions } from '@handline/app-data';
import { setting } from './daemon.ts';

export function Compose({ agent, storage, canSend, history }: { agent: string; storage: KV | null; canSend: boolean; history: string[] }) {
  const act = useChatActions(agent);
  const draft = setting(storage, `handline.draft.${agent}`, '');
  const [text, setText] = useState(draft.value);
  const back = useRef(-1);
  const set = (v: string) => {
    setText(v);
    draft.save(v);
  };
  // The message shows at once and the box clears; a refused one stays in the list as a failed row with Retry, so its words are not lost.
  const send = () => {
    if (!text.trim()) return;
    act.send(text);
    set('');
    back.current = -1;
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    } else if (e.key === 'ArrowUp' && (text === '' || back.current >= 0) && history.length) {
      e.preventDefault();
      back.current = Math.min(back.current + 1, history.length - 1);
      setText(history[back.current]!);
    } else if (e.key === 'ArrowDown' && back.current >= 0) {
      e.preventDefault();
      back.current -= 1;
      setText(back.current >= 0 ? history[back.current]! : '');
    }
  };
  return (
    <div className="compose-row">
      <textarea
        className="compose"
        rows={2}
        value={text}
        disabled={!canSend}
        aria-label="Message to the agent"
        placeholder={canSend ? 'Message the agent (Enter sends, Shift+Enter for a new line)' : 'This daemon does not accept chat messages'}
        onChange={(e) => {
          back.current = -1;
          set(e.target.value);
        }}
        onKeyDown={onKey}
      />
      <button className="btn primary" disabled={!canSend || !text.trim()} onClick={send}>
        Send ↵
      </button>
    </div>
  );
}
