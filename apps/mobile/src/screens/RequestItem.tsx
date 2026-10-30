// One Requests item, full screen: back to the list, "n of N" with up and down, the item's header and why, and its own answers in a card fixed at
// the bottom. An answer is held five seconds (Undo) and the next item opens; a worktree answer and Ask for change are sent at once.
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import {
  APPROVED,
  VERBS,
  WORKTREE_WORDS,
  ago,
  cardActionLabel,
  changeAsked,
  dueText,
  sourceFull,
  spareNotice,
  worktreeFacts,
  worktreeSaid,
  type Card,
  type CardAction,
} from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { classify, sentence, useTasks, useVerbRun, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import type { Held, Step } from '#src/heldAnswers.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { AskChange, ReviewPictures } from '#src/screens/ReviewAsk.tsx';
import { SpareTasks } from '#src/screens/SpareCard.tsx';
import { Button, MenuItem, MenuSheet } from '#src/screens/parts.tsx';

const BIG = { minHeight: 48 } as const;

export function RequestItem(props: {
  card: Card;
  /** Where it stands in the list as read top down, 1-based, and how many there are. */
  at: { n: number; of: number };
  held: Held;
  onBack: () => void;
  onStep: (by: -1 | 1) => void;
  /** An answer was given: the next item opens. */
  onAnswered: () => void;
  onTask: (id: number) => void;
}) {
  const { card, held } = props;
  const c = usePalette();
  const store = useStore();
  const done = useWrite('task.done');
  const read = useWrite('inbox.mark_read');
  const verb = useVerbRun(card.agent ?? '');
  // A weekly-loss offer is a task of the person's: its item answers it with Start or Dismiss.
  const spare = (useTasks({}).data?.items ?? []).flatMap((t) => (t.id === card.task ? (spareNotice(t) ?? []) : []))[0];
  const [now] = useState(() => Date.now());
  const [text, setText] = useState('');
  const [change, setChange] = useState(false);
  const [sure, setSure] = useState(false);
  const [asking, setAsking] = useState(false);
  const [more, setMore] = useState(false);
  const refused = [done, read].find((w) => w.isError)?.error;

  const hold = (line: string, steps: Step[]) => (held.hold(card.key, line, steps), props.onAnswered());
  const finish = (task: number, evidence: string): Step => ({ method: 'task.done', params: { task, evidence, reason: true } });
  const markRead = (id: number): Step => ({ method: 'inbox.mark_read', params: { ids: [id] } });
  const open = () => {
    if (card.task != null) props.onTask(card.task);
    else if (card.agent) void store.openAgent(card.agent);
  };
  const run = (a: CardAction) => {
    switch (a.type) {
      case 'answer':
        return;
      case 'change':
        return setChange(true);
      case 'reply':
        return hold('Answer sent', [{ method: 'inbox.reply', params: { id: a.id, text: a.text } }]);
      case 'allow':
      case 'deny':
        return hold(a.type === 'allow' ? 'Allowed' : 'Denied', [
          { method: 'prompt.answer', params: { agent: a.agent, prompt: a.prompt, choice: a.choice } },
        ]);
      case 'done':
        return hold('Done', [finish(a.item.task!, 'decided in Requests'), markRead(a.item.id)]);
      case 'done_task':
        return hold('Done', [finish(a.task, 'Done')]);
      case 'approve':
        return hold('Approved', [finish(a.item.task!, APPROVED), markRead(a.item.id)]);
      case 'ask_change':
        return setAsking(true);
      case 'worktree': {
        if (a.answer === 'force' && !sure) return setSure(true);
        setSure(false);
        // The line and the read mark wait for the daemon: it can refuse the removal, and the item then stays.
        return void done.mutateAsync({ task: a.item.task!, evidence: WORKTREE_WORDS[a.answer], reason: true }).then(
          () => {
            store.say(worktreeSaid(a.answer, worktreeFacts(card.detail).member));
            read.mutate({ ids: [a.item.id] });
            props.onAnswered();
          },
          () => undefined,
        );
      }
      case 'open':
        return open();
      case 'terminal':
        return void store.openAgent(a.agent).then(() => store.setTab('terminal'));
      case 'retry':
        return verb(VERBS.clear_stall);
      case 'read':
        return hold('Marked read', [markRead(a.id)]);
    }
  };
  const wt = card.actions.some((a) => a.type === 'worktree');
  const main = card.actions.slice(0, card.primary).filter((a) => a.type !== 'answer' && a.type !== 'open');
  const rest = card.actions.slice(card.primary).filter((a) => a.type !== 'answer');
  const openAct = card.actions.slice(0, card.primary).find((a) => a.type === 'open');
  const answer = wt ? undefined : card.actions.find((a) => a.type === 'answer');
  const sendAnswer = () => {
    if (!text.trim() || answer?.type !== 'answer') return;
    hold('Answer sent', [{ method: 'inbox.reply', params: { id: answer.id, text: change ? changeAsked(text) : text.trim() } }]);
    setChange(false);
    setText('');
  };
  const testID = (a: CardAction) => `request-act-${a.type}${a.type === 'worktree' ? `-${a.answer}` : ''}`;
  const facts = wt ? worktreeFacts(card.detail).facts : card.detail;
  const small = { color: c.muted, fontSize: font.size.sm } as const;
  return (
    <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }} testID="request-page">
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: space.sm, minHeight: 48 }}>
        <Pressable
          testID="request-back"
          accessibilityRole="button"
          accessibilityLabel="Requests"
          onPress={props.onBack}
          style={{ flexDirection: 'row', alignItems: 'center', minHeight: 44, paddingHorizontal: space.sm, gap: space.xs }}
        >
          <Icon name="back" decorative size={18} color={c.accent} />
          <Text style={{ color: c.accent, fontSize: font.size.md }}>Requests</Text>
        </Pressable>
        <View style={{ flex: 1 }} />
        <Text
          testID="request-position"
          style={{ color: c.muted, fontSize: font.size.md, marginRight: space.sm }}
        >{`${props.at.n} of ${props.at.of}`}</Text>
        <StepButton testID="request-prev" label="Previous" icon="less" disabled={props.at.n <= 1} onPress={() => props.onStep(-1)} />
        <StepButton
          testID="request-next"
          label="Next"
          icon="chevron"
          disabled={props.at.n >= props.at.of}
          onPress={() => props.onStep(1)}
        />
      </View>
      <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.md }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' }}>
          <Pressable
            testID="request-source"
            accessibilityRole="button"
            accessibilityLabel={`Open ${sourceFull(card)}`}
            disabled={card.kind === 'machine'}
            onPress={open}
            style={{ borderWidth: 1, borderColor: c.line, borderRadius: radius.s, paddingHorizontal: space.sm, paddingVertical: 2 }}
          >
            <Text style={{ color: c.ink, fontSize: font.size.xs }}>{sourceFull(card)}</Text>
          </Pressable>
          {card.from ? <Text style={small}>{card.from}</Text> : null}
          <Text style={small}>
            {[card.due != null ? dueText(card.due, now) : '', card.since ? `waiting ${ago(new Date(card.since).toISOString())}` : '']
              .filter(Boolean)
              .join(' · ') || 'now'}
          </Text>
        </View>
        <Text style={{ color: c.ink, fontSize: font.size.lg, fontWeight: '700' }}>{card.title}</Text>
        {spare ? <SpareTasks n={spare} /> : facts ? <Text style={{ color: c.ink }}>{facts}</Text> : null}
        {card.review && card.task != null ? <ReviewPictures task={card.task} /> : null}
      </ScrollView>
      <View
        testID="request-answers"
        style={{ borderTopWidth: 1, borderTopColor: c.line, backgroundColor: c.panel, padding: space.md, gap: space.sm }}
      >
        {refused ? (
          <Text accessibilityRole="alert" style={{ color: c.block, fontSize: font.size.sm }}>
            {sentence(classify(refused))}
          </Text>
        ) : null}
        {answer && !card.review ? (
          <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-end' }}>
            <TextInput
              testID="request-text"
              multiline
              value={text}
              onChangeText={setText}
              placeholder={change ? 'What to change' : 'Write an answer'}
              placeholderTextColor={c.muted}
              accessibilityLabel="Your answer"
              style={{
                flex: 1,
                color: c.ink,
                borderWidth: 1,
                borderColor: c.line,
                borderRadius: radius.m,
                padding: space.md,
                minHeight: 48,
              }}
            />
            <Button testID="request-send" kind="yes" label="Send" disabled={!text.trim()} onPress={sendAnswer} style={BIG} />
          </View>
        ) : null}
        {sure ? (
          <View style={{ gap: space.sm }}>
            <Text style={{ color: c.block }}>Remove for good? Uncommitted files are lost.</Text>
            <View style={{ flexDirection: 'row', gap: space.sm }}>
              <Button
                testID="request-sure"
                kind="no"
                label="Remove"
                style={{ ...BIG, flex: 1 }}
                onPress={() => run(main.find((a) => a.type === 'worktree' && a.answer === 'force')!)}
              />
              <Button testID="request-sure-cancel" label="Cancel" style={{ ...BIG, flex: 1 }} onPress={() => setSure(false)} />
            </View>
          </View>
        ) : (
          <View style={{ flexDirection: 'row', gap: space.sm, flexWrap: 'wrap' }}>
            {spare ? (
              <>
                <Button
                  testID="request-start"
                  kind="yes"
                  icon="play"
                  label={spare.ids.length === 1 ? 'Start 1 task' : `Start ${spare.ids.length} tasks`}
                  disabled={!spare.ids.length}
                  style={{ ...BIG, flex: 1 }}
                  onPress={() => hold('Started', [finish(spare.task, 'start')])}
                />
                <Button
                  testID="request-dismiss"
                  label="Dismiss"
                  style={{ ...BIG, flex: 1 }}
                  onPress={() => hold('Dismissed', [finish(spare.task, 'dismiss')])}
                />
              </>
            ) : (
              main.map((a, i) => (
                <Button
                  key={a.type + i}
                  testID={testID(a)}
                  kind={i === 0 ? 'yes' : a.type === 'deny' || (a.type === 'worktree' && a.answer === 'force') ? 'no' : undefined}
                  label={cardActionLabel(a)}
                  style={{ ...BIG, flex: 1 }}
                  onPress={() => run(a)}
                />
              ))
            )}
            {rest.length ? <Button testID="request-more" label="More" style={BIG} onPress={() => setMore(true)} /> : null}
          </View>
        )}
        {openAct ? (
          <Pressable
            testID="request-open"
            accessibilityRole="button"
            onPress={() => run(openAct)}
            style={{ minHeight: 44, justifyContent: 'center' }}
          >
            <Text style={{ color: c.accent }}>{card.task != null ? `Open T${card.task}` : 'Open'}</Text>
          </Pressable>
        ) : null}
      </View>
      {more ? (
        <MenuSheet testID="request-menu" onClose={() => setMore(false)}>
          {rest.map((a, i) => (
            <MenuItem key={a.type + i} testID={testID(a)} label={cardActionLabel(a)} onPress={() => (setMore(false), run(a))} />
          ))}
        </MenuSheet>
      ) : null}
      {asking && card.task != null ? (
        <AskChange
          task={card.task}
          onClose={() => setAsking(false)}
          onSent={(words) => {
            setAsking(false);
            store.say(`Change asked: ${words.trim()}`);
            props.onAnswered();
          }}
        />
      ) : null}
    </KeyboardAvoidingView>
  );
}

function StepButton(props: { testID: string; label: string; icon: 'less' | 'chevron'; disabled: boolean; onPress: () => void }) {
  const c = usePalette();
  return (
    <Pressable
      testID={props.testID}
      accessibilityRole="button"
      accessibilityLabel={props.label}
      disabled={props.disabled}
      onPress={props.onPress}
      style={{ width: 44, height: 44, alignItems: 'center', justifyContent: 'center', opacity: props.disabled ? 0.3 : 1 }}
    >
      <Icon name={props.icon} decorative size={20} color={c.ink} />
    </Pressable>
  );
}
