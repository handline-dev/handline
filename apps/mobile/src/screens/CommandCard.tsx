// A fenced sh, bash or shell block of a message as a command card: the folder it runs in, quiet, at the top left, and two icons at the top right,
// copy (turns into a check with a small "Copied" to its left for 1.5 s) and run (confirmed first, runs on the computer through the daemon). Under the
// card, how the last run went. Without a chat message around it (a task comment, a file) the card has no run: copy only.
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { Pressable, Text, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { commandRisk, runLine } from '@handline/app-core';
import { useServes, useWrite } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { BottomSheet, Button } from '#src/screens/parts.tsx';

const COPIED_MS = 1500;
const HIT = 44;

type Run = { block: string; state: 'running' | 'done' | 'failed' | 'stopped'; exit?: number; seconds?: number; started_at: string };

/** The agent a chat is with and the folder its commands run in. */
export const CommandHome = createContext<{ agent: string; cwd?: string } | null>(null);
/** The message a block is in, with the runs it has so far. */
export const CommandMessage = createContext<{ id: number; runs?: Run[] } | null>(null);

/** The folder as a person writes it: under the home directory as ~. */
const quiet = (cwd: string) => cwd.replace(/^\/(Users|home)\/[^/]+/, '~');

export function CommandMessageScope(props: { id: number; runs?: Run[]; children: ReactNode }) {
  return <CommandMessage.Provider value={{ id: props.id, runs: props.runs }}>{props.children}</CommandMessage.Provider>;
}

export function CommandCard({ text, index }: { text: string; index: number }) {
  const c = usePalette();
  const home = useContext(CommandHome);
  const message = useContext(CommandMessage);
  const founder = useStore().client.hello?.role === 'founder';
  const { served, open } = useServes('shell.open');
  const [copied, setCopied] = useState(false);
  const [asking, setAsking] = useState(false);
  const shell = useWrite('shell.open', home?.agent);
  const block = message ? `${message.id}:${index}` : null;
  const run = message?.runs?.find((r) => r.block === block);
  const canRun = !!home && !!block && founder && served;
  const risk = commandRisk(text);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(t);
  }, [copied]);

  const folder = home?.cwd ? quiet(home.cwd) : null;
  return (
    <View>
      <View
        testID={`command-${index}`}
        style={{ backgroundColor: c.line, borderRadius: radius.m, paddingHorizontal: space.sm, paddingBottom: space.sm }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', minHeight: HIT }}>
          <Text numberOfLines={1} style={{ flex: 1, color: c.muted, fontFamily: 'monospace', fontSize: font.size.xs }}>
            {folder ?? ''}
          </Text>
          {copied && (
            <Text testID={`command-copied-${index}`} style={{ color: c.muted, fontSize: font.size.xs }}>
              Copied
            </Text>
          )}
          <IconButton
            testID={`command-copy-${index}`}
            label="Copy"
            onPress={() => void Clipboard.setStringAsync(text).then(() => setCopied(true))}
            icon={copied ? 'delivered' : 'copy'}
            iconLabel={copied ? 'Copied' : 'Copy'}
            color={c.ink}
          />
          {canRun && open && (
            <IconButton
              testID={`command-run-${index}`}
              label={`Run in ${folder ?? 'the agent folder'}`}
              onPress={() => setAsking(true)}
              icon="play"
              iconLabel="Run"
              color={risk ? c.block : c.ink}
            />
          )}
        </View>
        <Text selectable style={{ color: c.ink, fontFamily: 'monospace', fontSize: font.size.sm }}>
          {text}
        </Text>
      </View>
      {canRun && !open && (
        <Text testID={`command-offline-${index}`} style={{ color: c.muted, fontSize: font.size.xs, paddingTop: space.xs }}>
          Run needs your computer, which is not reachable now.
        </Text>
      )}
      {run && <RunLine index={index} run={run} />}
      {shell.error ? (
        <Text
          testID={`command-error-${index}`}
          accessibilityRole="alert"
          style={{ color: c.block, fontSize: font.size.xs, paddingTop: space.xs }}
        >
          The command did not start. Try again.
        </Text>
      ) : null}
      {asking && home && block && (
        <BottomSheet testID={`command-confirm-${index}`} padded onClose={() => setAsking(false)}>
          <Text style={{ color: c.ink, fontSize: font.size.lg, fontWeight: '700' }}>Run on your computer</Text>
          <Text style={{ color: c.ink }}>Run in {folder ?? 'the agent folder'}? It runs on your computer as you.</Text>
          {risk && (
            <Text testID={`command-risk-${index}`} style={{ color: c.block }}>
              {risk}
            </Text>
          )}
          <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: space.md, paddingTop: space.sm }}>
            <Button testID={`command-cancel-${index}`} label="Cancel" onPress={() => setAsking(false)} />
            <Button
              testID={`command-go-${index}`}
              kind={risk ? 'no' : 'yes'}
              label="Run"
              onPress={() => {
                setAsking(false);
                shell.mutate({ agent: home.agent, command: text, block });
              }}
            />
          </View>
        </BottomSheet>
      )}
    </View>
  );
}

function IconButton(props: {
  testID: string;
  label: string;
  icon: 'copy' | 'play' | 'delivered';
  iconLabel: string;
  color: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      testID={props.testID}
      accessibilityRole="button"
      accessibilityLabel={props.label}
      onPress={props.onPress}
      style={{ width: HIT, height: HIT, alignItems: 'center', justifyContent: 'center' }}
    >
      <Icon name={props.icon} color={props.color} size={18} decorative />
    </Pressable>
  );
}

/** "Running · 4s", "Done · exit 0 · 3s", "Failed · exit 1 · 3s" or "Stopped"; a running line counts up. */
function RunLine({ index, run }: { index: number; run: Run }) {
  const c = usePalette();
  const [now, setNow] = useState(() => Date.now());
  const running = run.state === 'running';
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  return (
    <Text
      testID={`command-result-${index}`}
      style={{ color: run.state === 'failed' ? c.block : c.muted, fontSize: font.size.xs, paddingTop: space.xs }}
    >
      {run.state === 'done' ? '✓ ' : ''}
      {runLine(run, now)}
    </Text>
  );
}
