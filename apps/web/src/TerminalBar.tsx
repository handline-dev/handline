// Close terminal and Open terminal above the terminal tab: Close frees the host's terminal (refused while a session runs), Open says where the terminal is and how to attach.
import { useState } from 'react';
import type { AgentDetail } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { classify, sentence, useOpenTerminal, useWrite } from '@handline/app-data';
import { Button } from './ui/index.ts';

type Opening = { host: string; attach: { argv: string[] } | null };

export function TerminalBar({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const served = useSelector((s) => s.served);
  const close = useWrite('terminal.close', agent);
  const [opening, setOpening] = useState<Opening | null>(null);
  const [note, setNote] = useState('');
  const ask = useOpenTerminal(agent);
  const canClose = served.has('terminal.close'),
    canOpen = served.has('terminal.open');
  if (!detail.terminal || (!canClose && !canOpen)) return null;
  const fail = (e: unknown) => setNote(sentence(classify(e)));
  const open = () => {
    setNote('');
    ask.mutate(undefined, {
      onSuccess: (r) => setOpening({ host: r.host, attach: r.attach }),
      onError: (e) => (setOpening(null), fail(e)),
    });
  };
  const command = opening?.attach?.argv.join(' ') ?? '';
  return (
    <div className="terminal-bar flex flex-none flex-wrap items-center gap-2 border-b border-line px-2.5 py-1.5 text-[12px]">
      {canOpen ? <Button size="sm" label="Open terminal" disabled={ask.isPending} onClick={open} /> : null}
      {canClose ? (
        <Button
          size="sm"
          label="Close terminal"
          disabled={close.isPending || detail.session_open}
          onClick={() => {
            setNote('');
            close.mutate({ agent }, { onError: fail });
          }}
        />
      ) : null}
      {canClose && detail.session_open ? <span className="muted">Stop the session first to close its terminal.</span> : null}
      {opening ? (
        <span className="flex items-center gap-2">
          <span>Terminal on {opening.host}</span>
          {command ? (
            <>
              <code>{command}</code>
              <Button size="sm" label="Copy" onClick={() => void navigator.clipboard?.writeText(command)} />
            </>
          ) : (
            <span className="muted">Open it in {opening.host}'s own app.</span>
          )}
        </span>
      ) : null}
      {note ? <span role="alert">{note}</span> : null}
    </div>
  );
}
