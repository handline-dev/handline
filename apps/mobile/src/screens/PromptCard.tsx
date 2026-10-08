// The card for a prompt the agent is stuck on, level with the web one: a permission (Yes or No, never "always"), a dialog (its own options,
// "always" ones only when the daemon can keep a rule), a question with numbered options and a line of your own, and for a multi prompt
// checkboxes with a Submit. The answer is a decided write: the tapped option shows "…", the rest are disabled, and a prompt that has
// gone sends nothing and says so.
import { useEffect, useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { isAlways, promptChoices, promptTitle } from '@handline/app-core';
import { usePending, useWrite } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import type { Prompt } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Button, Row } from './parts.tsx';

const code = (e: unknown) => (e as { code?: string } | null)?.code;

function PromptCard(props: { agent: string; prompt: Prompt; onStale: () => void }) {
  const c = usePalette();
  const { agent, prompt: p } = props;
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
  const cancel = p.kind === 'question' ? all.find((o) => o.key === 'esc') : undefined; // its own button, never a checkbox or a numbered row
  const opts = cancel ? all.filter((o) => o !== cancel) : all;
  const multi = p.multi === true;
  const question = p.kind === 'question';
  const send = (choice: string | string[] | { text: string }, mark: string) => {
    setClicked(mark);
    setChanged(false);
    // mutateAsync: the card unmounts when a refused answer finds no prompt left, and the slot must still hear of it
    answer.mutateAsync({ agent, prompt: p.id, choice }).catch((e) => {
      setClicked(null);
      if (code(e) !== 'stale_prompt') return;
      setChanged(true);
      props.onStale();
    });
  };
  const toggle = (key: string) => setPicked((l) => (l.includes(key) ? l.filter((k) => k !== key) : [...l, key]));
  const mark = (key: string, label: string) => (answering && clicked === key ? '…' : label);
  const what = p.command ? `${p.tool ?? ''} ${p.command}` : p.lines.join('\n');
  return (
    <View
      testID="prompt-card"
      accessibilityRole="alert"
      style={{
        margin: space.md,
        padding: space.md,
        gap: space.sm,
        backgroundColor: c.panel,
        borderColor: c.wait,
        borderWidth: 1,
        borderRadius: radius.lg,
      }}
    >
      <Text style={{ color: c.ink, fontWeight: '700' }}>
        ⚠ {promptTitle(p)}
        {p.reason ? ` · ${p.reason}` : ''}
        {answering ? ' · answering…' : ''}
      </Text>
      {changed && (
        <Text testID="prompt-changed" style={{ color: c.wait }}>
          This prompt changed
        </Text>
      )}
      <Text style={{ color: c.ink, fontFamily: 'Menlo', fontSize: 12 }}>{what}</Text>
      {question || multi ? (
        <View style={{ gap: space.sm }}>
          {opts.map((o, i) => (
            <Button
              key={o.key}
              testID={`answer-${o.key}`}
              label={multi ? `${picked.includes(o.key) ? '☑' : '☐'} ${o.label}` : `${o.key || i + 1}  ${mark(o.key, o.label)}`}
              kind={multi && picked.includes(o.key) ? 'yes' : 'plain'}
              disabled={answering}
              onPress={() => (multi ? toggle(o.key) : send(o.key, o.key))}
            />
          ))}
        </View>
      ) : (
        <Row style={{ flexWrap: 'wrap' }}>
          {opts.map((o) => (
            <Button
              key={o.key}
              testID={`answer-${o.key}`}
              label={`${isAlways(o) ? '↻ ' : ''}${mark(o.key, o.label)}`}
              kind={o.answer === 'yes' || (p.kind !== 'permission' && o.highlighted) ? 'yes' : o.answer === 'no' ? 'no' : 'plain'}
              disabled={answering}
              onPress={() => send(o.key, o.key)}
              style={{ flex: 1 }}
            />
          ))}
        </Row>
      )}
      {p.kind === 'dialog' && opts.some(isAlways) && <Text style={{ color: c.muted, fontSize: 12 }}>↻ saves a rule for next time</Text>}
      {multi && (
        <Button
          testID="answer-submit"
          label={mark('submit', 'Submit')}
          kind="yes"
          disabled={answering || !picked.length}
          onPress={() =>
            send(
              opts.map((o) => o.key).filter((k) => picked.includes(k)),
              'submit',
            )
          }
        />
      )}
      {question &&
        !multi &&
        (typing ? (
          <Row>
            <TextInput
              testID="answer-text"
              value={text}
              onChangeText={setText}
              editable={!answering}
              autoFocus
              accessibilityLabel="Your own answer"
              style={{ flex: 1, color: c.ink, borderColor: c.line, borderWidth: 1, borderRadius: radius.md, padding: space.md }}
            />
            <Button
              testID="answer-text-send"
              label={mark('text', 'Send')}
              kind="yes"
              disabled={answering || !text.trim()}
              onPress={() => send({ text: text.trim() }, 'text')}
            />
          </Row>
        ) : (
          <Button testID="answer-type" label="Type something" disabled={answering} onPress={() => setTyping(true)} />
        ))}
      {cancel && (
        <Button
          testID={`answer-${cancel.key}`}
          label={mark(cancel.key, 'Cancel')}
          disabled={answering}
          onPress={() => send(cancel.key, cancel.key)}
        />
      )}
    </View>
  );
}

/** The card, and what stays when the prompt goes after a refused answer: "Answered elsewhere" for 4 s. */
export function PromptSlot(props: { agent: string; prompt: Prompt | null }) {
  const c = usePalette();
  const [gone, setGone] = useState(false);
  const [refused, setRefused] = useState(false);
  useEffect(() => {
    if (!props.prompt && refused) setGone(true);
    setRefused(false); // a refusal that left a prompt open is over once the card shows it
  }, [props.prompt, refused]);
  useEffect(() => {
    if (!gone) return;
    const t = setTimeout(() => setGone(false), 4000);
    return () => clearTimeout(t);
  }, [gone]);
  if (props.prompt) return <PromptCard agent={props.agent} prompt={props.prompt} onStale={() => setRefused(true)} />;
  return gone ? (
    <Text testID="prompt-gone" style={{ color: c.muted, padding: space.md }}>
      Answered elsewhere
    </Text>
  ) : null;
}
