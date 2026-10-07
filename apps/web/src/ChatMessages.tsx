// One line of the conversation: the person's and the agent's messages with how far each got, and the transcript's tool, prompt and task lines.
import { useState } from 'react';
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { canWithdraw, isOpenAsk, landingLabel, sideOf } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { useAgents, useChatActions } from '@handline/app-data';
import { EditorButton } from './EditorButton.tsx';
import { PathText } from './PathText.tsx';

const clock = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(+d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

export function Message({ m, agent }: { m: ChatMessage; agent: string }) {
  const act = useChatActions(agent);
  const can = useSelector((s) => ({ withdraw: s.can('chat.withdraw'), answer: s.can('chat.answer'), dismiss: s.can('chat.dismiss') }));
  const names = new Set((useAgents().data?.agents ?? []).map((a) => a.name));
  const side = sideOf(m, agent, (n) => names.has(n));
  const [answer, setAnswer] = useState('');
  const unnumbered = m.id < 0; // shown before the daemon numbered it (or failed): nothing can be done to it but Retry and Discard
  const failed = m.state === 'failed';
  return (
    <div
      className={`msg ${side}${failed ? ' failed' : ''}`}
      data-landing={m.landing}
      data-id={m.id}
      data-pending={unnumbered && !failed ? 'true' : undefined}
    >
      {side === 'other' ? (
        <div className="msg-from muted">
          {m.from} → {m.to}
        </div>
      ) : null}
      <div className="msg-body">
        <PathText text={m.body} agent={agent} />
      </div>
      <div className="msg-meta muted">
        {clock(m.at)}
        {side === 'person' && !failed ? (
          <span className="landing" title={m.sending_reason ?? undefined}>
            {' · '}
            {landingLabel(m.landing)}
            {m.sending_reason && m.landing === 'queued' ? `: ${m.sending_reason}` : ''}
          </span>
        ) : null}
        {!unnumbered && canWithdraw(m, agent) && can.withdraw ? (
          <button className="link" onClick={() => act.withdraw(m.id)}>
            Withdraw
          </button>
        ) : null}
      </div>
      {failed ? (
        <div className="msg-meta failed-line" role="alert">
          Not sent: {m.sending_reason}{' '}
          <button className="link" onClick={() => act.retry(m.id)}>
            Retry
          </button>
          <button className="link" onClick={() => act.discard(m.id)}>
            Discard
          </button>
        </div>
      ) : null}
      {side === 'agent' && m.answer ? (
        <div className="msg person answer">
          <div className="msg-body">
            <PathText text={m.answer.text} agent={agent} />
          </div>
          <div className="msg-meta muted">{clock(m.answer.at)} · Your answer</div>
        </div>
      ) : null}
      {side === 'agent' && m.dismissed ? <div className="msg-meta muted">Dismissed</div> : null}
      {isOpenAsk(m, agent) && can.answer ? (
        <form
          className="ask"
          onSubmit={(e) => {
            e.preventDefault();
            if (!answer.trim()) return;
            act.answer(m.id, answer);
            setAnswer('');
          }}
        >
          <input value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Answer" aria-label={`Answer to message ${m.id}`} />
          <EditorButton text={answer} onText={setAnswer} />
          <button className="btn primary" disabled={!answer.trim()}>
            Answer
          </button>
          {can.dismiss ? (
            <button type="button" className="btn" onClick={() => act.dismiss(m.id)}>
              Dismiss
            </button>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}

export function Item({ i }: { i: TranscriptItem }) {
  if (i.kind === 'reply')
    return (
      <div className="msg agent" data-kind="reply">
        <div className="msg-body">
          <PathText text={i.text ?? ''} />
        </div>
        <div className="msg-meta muted">{clock(i.at)}</div>
      </div>
    );
  if (i.kind === 'tool')
    return (
      <div className="line tool" data-kind="tool">
        ⚙ {i.tool}
        {i.text ? <span className="muted"> {i.text}</span> : null}
      </div>
    );
  if (i.kind === 'prompt')
    return (
      <div className="line prompt-line" data-kind="prompt">
        ⚠ {i.prompt?.kind === 'permission' ? `Asked permission: ${i.prompt.tool ?? ''}` : `Asked: ${i.prompt?.kind ?? 'a dialog'}`}{' '}
        <span className="muted">{i.open ? '· waiting' : '· answered'}</span>
      </div>
    );
  const e = i.task_event;
  return (
    <div className="line event" data-kind="task_event">
      Task #{e?.task} {e?.event}
      {e?.evidence ? <span className="muted"> — {e.evidence}</span> : null}
    </div>
  );
}
