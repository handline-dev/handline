// The two things a task's detail does with people: hand the task to someone (with what to tell them), and answer someone it names.
import { useState } from 'react';
import { assignGroups, replyTargets, taskRef } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { useAgents, useChatActions, useTeam, useWrite } from '@handline/app-data';
import { Button, Textarea } from './ui/index.ts';
import { Sheet, useCan } from './taskui.tsx';

const CHIP = 'rounded-full border px-2 py-0.5 text-label';
const chipLook = (on: boolean) =>
  on ? 'border-primary bg-primary text-on-primary' : 'border-line-strong bg-transparent text-ink hover:bg-raised';

/** Who the task can go to, in three labelled groups; one is picked, then what to say with it. */
export function AssignSheet({ t, me, onClose }: { t: Detail; me: string; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const chat = useChatActions(t.to);
  const known = useAgents().data?.agents.map((a) => a.name) ?? [];
  const team = useTeam().data ?? { agents: [] };
  const groups = assignGroups(t, team, known, me);
  const [to, setTo] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const submit = () => {
    if (!to) return;
    const text = note.trim();
    edit.mutate(
      { task: t.id, owner: to },
      {
        onSuccess: () => {
          if (text) chat.send(`Task ${taskRef(t.id)} (${t.title}) is yours now. ${text}`, { to });
          onClose();
        },
      },
    );
  };
  return (
    <Sheet
      label={`Assign ${taskRef(t.id)} to…`}
      submit={to ? `Assign to ${to}` : 'Pick someone'}
      onCancel={onClose}
      onSubmit={submit}
      busy={edit.isPending}
    >
      <div className="grid gap-2">
        {groups.length ? (
          groups.map((g) => (
            <section key={g.key} aria-label={g.label}>
              <div className="mb-1 text-label text-ink-3">{g.label}</div>
              <div className="flex flex-wrap gap-1.5">
                {g.names.map((n) => (
                  <button
                    key={n}
                    type="button"
                    aria-pressed={to === n}
                    className={`${CHIP} ${chipLook(to === n)}`}
                    onClick={() => setTo(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </section>
          ))
        ) : (
          <span className="muted">There is no one else to assign it to.</span>
        )}
        <Textarea
          grow
          className="tinput"
          rows={3}
          value={note}
          aria-label="What to tell them"
          placeholder={to ? `What to tell ${to} (optional)` : 'What to tell them (optional)'}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
    </Sheet>
  );
}

/** The people the task names as buttons; pick one, type, send. */
export function ReplyBox({ t, me, box }: { t: Detail; me: string; box: React.RefObject<HTMLTextAreaElement | null> }) {
  const can = useCan('chat.send');
  const people = replyTargets(t, me);
  const [to, setTo] = useState<string | null>(null);
  const [text, setText] = useState('');
  const chat = useChatActions(to ?? t.to);
  const body = text.trim();
  const send = () => {
    if (!to || !body) return;
    chat.send(`Re task ${taskRef(t.id)} (${t.title}): ${body}`, { to });
    setText('');
  };
  return (
    <section className="mt-4 grid gap-2" aria-label="Reply to someone">
      <h3 className="m-0 text-ink-3">Reply to</h3>
      <div className="flex flex-wrap gap-1.5">
        {people.map((n) => (
          <button
            key={n}
            type="button"
            aria-pressed={to === n}
            className={`${CHIP} ${chipLook(to === n)}`}
            onClick={() => (setTo(n), box.current?.focus())}
          >
            {n}
          </button>
        ))}
        {people.length ? null : <span className="muted">No one else is on this task.</span>}
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <Textarea
          ref={box}
          grow
          className="tinput"
          rows={2}
          value={text}
          disabled={!can.ok || !people.length}
          title={can.why}
          placeholder={to ? `Message to ${to}` : 'Pick who to reply to first'}
          aria-label="Reply"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey)) return;
            e.preventDefault();
            send();
          }}
        />
        <Button
          type="submit"
          variant="solid"
          icon="send"
          label={to ? `Send to ${to}` : 'Send'}
          disabled={!can.ok || !to || !body}
          title={can.why}
        >
          {to ? `Send to ${to}` : 'Send'}
        </Button>
      </form>
    </section>
  );
}
