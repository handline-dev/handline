// The machine's disk need in My turn, and for the founder the worktrees no agent works in (worktrees.list orphans), largest first, each
// removed by worktree.remove. One that holds work is refused until the founder says to remove it anyway.
import { useState } from 'react';
import { Text, View } from 'react-native';
import { bytesText, heldWords } from '@handline/app-core';
import type { Worktree } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useServes, useWorktrees, useWrite } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

const name = (path: string) => path.split('/').filter(Boolean).at(-1) ?? path;

export function DiskCard(props: { detail: string }) {
  const c = usePalette();
  const founder = useStore().client.hello?.role === 'founder';
  const served = useServes('worktree.remove').served && founder;
  const list = useWorktrees();
  const orphans = (list.data?.worktrees ?? []).filter((w) => w.orphan).sort((a, b) => (b.size_bytes ?? 0) - (a.size_bytes ?? 0));
  const [open, setOpen] = useState(false);
  const size = orphans.reduce((n, w) => n + (w.size_bytes ?? 0), 0);
  return (
    <View testID="disk-card" style={{ padding: space.lg, gap: space.sm, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ color: c.muted, fontSize: font.size.xs }}>Machine</Text>
      <Text style={{ color: c.ink, fontWeight: '600' }}>Free disk space</Text>
      {props.detail ? <Text style={{ color: c.ink }}>{props.detail}</Text> : null}
      {served && orphans.length > 0 ? (
        <Button
          testID="disk-orphans-open"
          label={`Leftover worktrees: ${orphans.length}${size ? `, ${bytesText(size)}` : ''}`}
          onPress={() => setOpen(true)}
        />
      ) : null}
      {open ? <Orphans orphans={orphans} onClose={() => setOpen(false)} /> : null}
    </View>
  );
}

function Orphans(props: { orphans: Worktree[]; onClose: () => void }) {
  const c = usePalette();
  const remove = useWrite('worktree.remove');
  const [gone, setGone] = useState<string[]>([]);
  const [asking, setAsking] = useState<{ w: Worktree; words: string } | null>(null);
  const [why, setWhy] = useState('');
  const rows = props.orphans.filter((w) => !gone.includes(w.path));
  const held = (w: Worktree) => heldWords([{ path: w.path, files_changed: w.files_changed ?? 0, commits_ahead: w.commits_ahead ?? 0 }]);
  const go = (w: Worktree, force: boolean) => {
    setWhy('');
    remove.mutate(
      { path: w.path, force },
      {
        onSuccess: () => {
          setGone((g) => [...g, w.path]);
          setAsking(null);
        },
        onError: (e) => {
          const f = classify(e);
          if (f.kind === 'refused' && f.gate === 'dirty' && !force) {
            setAsking({ w, words: held(w) || 'work that is not saved' });
          } else setWhy(sentence(f));
        },
      },
    );
  };
  if (asking)
    return (
      <Sheet label={`${name(asking.w.path)} holds work`} onCancel={() => setAsking(null)} error={why || undefined} id="orphan-ask">
        <Text style={{ color: c.muted }}>{`It has ${asking.words} that no agent keeps. Removing it anyway loses them.`}</Text>
        <Button testID="orphan-force" kind="no" label="Remove anyway" disabled={remove.isPending} onPress={() => go(asking.w, true)} />
      </Sheet>
    );
  return (
    <Sheet label="Leftover worktrees" onCancel={props.onClose} error={why || undefined} id="orphans">
      {rows.length === 0 ? <Text style={{ color: c.muted }}>None left.</Text> : null}
      {rows.map((w) => (
        <View key={w.path} style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <View style={{ flex: 1 }}>
            <Text numberOfLines={1} style={{ color: c.ink, fontWeight: '600' }}>
              {name(w.path)}
            </Text>
            <Text style={{ color: c.muted, fontSize: font.size.sm }}>
              {[w.size_bytes != null ? bytesText(w.size_bytes) : '', w.merged ? 'merged' : held(w)].filter(Boolean).join(' · ')}
            </Text>
          </View>
          <Button testID={`orphan-remove-${name(w.path)}`} label="Remove" disabled={remove.isPending} onPress={() => go(w, false)} />
        </View>
      ))}
    </Sheet>
  );
}
