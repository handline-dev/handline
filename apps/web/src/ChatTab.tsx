// Chat tab: the conversation, once the daemon serves it. Until then an empty state, and the permission card, which always shows.
import type { AgentDetail, Prompt } from '@handline/client';
import { offered } from '@handline/app-core';
import { usePending, useWrite } from '@handline/app-data';

export function PromptCard({ agent, prompt: p }: { agent: string; prompt: Prompt }) {
  const answer = useWrite('prompt.answer', agent);
  const answering = usePending('prompt.answer').some((v) => v.agent === agent);
  const what = p.kind === 'permission' ? `Permission: ${p.tool ?? ''}` : `Dialog: ${p.kind}`;
  return (
    <div className="card prompt" role="alert">
      <div className="card-title">⚠ {what}</div>
      {p.command ? <code className="cmd">{p.command}</code> : <pre className="lines">{p.lines.join('\n')}</pre>}
      {p.reason ? <p className="muted">{p.reason}</p> : null}
      <div className="card-actions">
        {offered(p).map((o) => (
          <button
            key={o.key}
            className={`btn${o.answer === 'yes' ? ' primary' : ''}`}
            disabled={answering}
            onClick={() => answer.mutate({ agent, prompt: p.id, choice: o.key })}
          >
            {o.label} <kbd>{o.key}</kbd>
          </button>
        ))}
      </div>
      {answering ? <p className="muted">answering…</p> : null}
    </div>
  );
}

export function ChatTab({ agent, detail }: { agent: string; detail: AgentDetail }) {
  return (
    <section className="chat">
      <div className="stream">
        <div className="empty">
          <strong>No chat yet</strong>
          <p>This daemon does not serve the conversation. Use the terminal tab to work with the agent.</p>
        </div>
      </div>
      <div className="cards">{detail.prompt ? <PromptCard agent={agent} prompt={detail.prompt} /> : null}</div>
      <div className="compose-row">
        <textarea
          className="compose"
          rows={2}
          placeholder="Chat is not available from this daemon yet"
          disabled
          aria-label="Message to the agent"
        />
        <button className="btn" disabled>
          Send ↵
        </button>
      </div>
    </section>
  );
}
