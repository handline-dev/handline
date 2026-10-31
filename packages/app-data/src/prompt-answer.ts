// The answer to a prompt the agent is stuck on, for the PC card and the phone's: what is picked or typed, the clicked option, the
// decided write (a prompt that has gone sends nothing and says so) and its pending mark.
import { useEffect, useState } from 'react';
import { promptChoices, splitCancel } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import type { Prompt } from '@handline/client';
import { usePending, useWrite } from '#src/write.ts';

const code = (e: unknown) => (e as { code?: string } | null)?.code;

export function usePromptAnswer(agent: string, p: Prompt, onStale: (() => void) | undefined) {
  const answer = useWrite('prompt.answer', agent);
  const answering = usePending('prompt.answer').some((v) => v.agent === agent);
  const rules = useSelector((s) => s.can('answer.add'));
  const [clicked, setClicked] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState('');
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    // a new prompt starts clean
    setPicked([]);
    setTyping(false);
    setText('');
    setClicked(null);
  }, [p.id]);

  const all = promptChoices(p, rules);
  const { cancel, opts } = splitCancel(p, all);
  const send = (choice: string | string[] | { text: string }, mark: string) => {
    setClicked(mark);
    setChanged(false);
    // mutateAsync: the card unmounts when a refused answer finds no prompt left, and the slot must still hear of it
    answer.mutateAsync({ agent, prompt: p.id, choice }).catch((e) => {
      setClicked(null);
      if (code(e) !== 'stale_prompt') return;
      setChanged(true);
      onStale?.();
    });
  };
  const toggle = (key: string) => setPicked((l) => (l.includes(key) ? l.filter((k) => k !== key) : [...l, key]));
  const mark = (key: string, label: string) => (answering && clicked === key ? '…' : label);
  return {
    answering,
    clicked,
    picked,
    typing,
    setTyping,
    text,
    setText,
    changed,
    all,
    cancel,
    opts,
    multi: p.multi === true,
    question: p.kind === 'question',
    send,
    toggle,
    mark,
  };
}
