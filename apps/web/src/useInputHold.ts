// Typing takes the input by itself and gives it back after a quiet spell, so no button stands between a person and the keyboard.
import { useEffect, useRef } from 'react';
import type { AgentDetail } from '@handline/client';
import { controlsOf } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { useWrite } from '@handline/app-data';

export const IDLE_MS = 20_000;

export function useInputHold(agent: string, detail: AgentDetail) {
  const store = useStore();
  const take = useWrite('input.take', agent);
  const release = useWrite('input.release', agent);
  const latest = useRef(detail);
  latest.current = detail; // the timer and the callers read the current detail without re-binding
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mine = useRef(false); // this tab took the input, so this tab gives it back
  const releaseNow = useRef(() => {});
  releaseNow.current = () => {
    clearTimeout(timer.current);
    if (!mine.current) return;
    mine.current = false;
    if (controlsOf(latest.current, store.me).release) void release.mutateAsync({ agent }).catch(() => {});
  };
  const touch = () => {
    clearTimeout(timer.current);
    if (mine.current) timer.current = setTimeout(() => releaseNow.current(), IDLE_MS);
  };
  /** True once this device may type: it already holds the input, or it was free to take and the take went through. */
  const ensure = async (): Promise<boolean> => {
    const c = controlsOf(latest.current, store.me);
    if (c.typing) return (touch(), true);
    if (!c.take) return false; // someone else holds it, or there is no session
    try {
      await take.mutateAsync({ agent });
    } catch {
      return false; // the write shows its own refusal
    }
    mine.current = true;
    touch();
    return true;
  };
  useEffect(() => () => releaseNow.current(), [agent]);
  return { ensure, touch, release: () => releaseNow.current() };
}
