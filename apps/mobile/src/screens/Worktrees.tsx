// The Worktrees page (docs/pc-app-gaps.md X5): every worktree as a card with its status chip and Move to…, and one worktree as a page.
// A status is a decided write: the card keeps its status, with a pending mark, until the refetch has read the new one.
import { useState } from 'react';
import { FlatList, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import type { Worktree } from '@handline/client';
import { classify, sentence, useServes, useWorktrees, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Button } from './parts.tsx';

export const STATUSES = ['todo', 'in progress', 'in review', 'done'];
const sums = (w: Worktree) => ({
  added: w.changed.reduce((n, f) => n + f.added, 0),
  deleted: w.changed.reduce((n, f) => n + f.deleted, 0),
});
const files = (w: Worktree) => {
  const { added, deleted } = sums(w);
  return `${w.changed.length} file${w.changed.length === 1 ? '' : 's'}${w.changed.length ? ` +${added} −${deleted}` : ''}`;
};

interface Moves {
  enabled: boolean;
  moving: Record<string, string>;
  errors: Record<string, string>;
  move: (path: string, status: string) => void;
}

/** Each write is asked once, pending until the refetch shows the new status, and refused with the daemon's reason. */
function useMoves(refetch: () => Promise<unknown>): Moves {
  const set = useWrite('worktree.set_status');
  const { served } = useServes('worktree.set_status');
  const [moving, setMoving] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const without = (r: Record<string, string>, k: string) => Object.fromEntries(Object.entries(r).filter(([p]) => p !== k));
  const move = (path: string, status: string) => {
    if (moving[path]) return;
    setMoving((m) => ({ ...m, [path]: status }));
    setErrors((e) => without(e, path));
    set
      .mutateAsync({ path, status })
      .then(() => refetch())
      .catch((e) => setErrors((x) => ({ ...x, [path]: sentence(classify(e)) })))
      .finally(() => setMoving((m) => without(m, path)));
  };
  return { enabled: served, moving, errors, move };
}

function Chip(props: { word: string }) {
  const c = usePalette();
  return (
    <Text
      testID="wt-status"
      style={{
        color: c.ink,
        borderWidth: 1,
        borderColor: c.line,
        borderRadius: radius.pill,
        paddingHorizontal: space.sm,
        fontSize: 12,
        overflow: 'hidden',
      }}
    >
      {props.word}
    </Text>
  );
}

/** The mark and the reason of a write on one worktree. */
function Notes({ w, moves }: { w: Worktree; moves: Moves }) {
  const c = usePalette();
  return (
    <>
      {moves.moving[w.path] ? (
        <Text testID="wt-moving" style={{ color: c.muted, fontSize: 12 }}>
          moving to {moves.moving[w.path]}…
        </Text>
      ) : null}
      {moves.errors[w.path] ? (
        <Text testID="wt-error" accessibilityRole="alert" style={{ color: c.block, fontSize: 12 }}>
          {moves.errors[w.path]}
        </Text>
      ) : null}
    </>
  );
}

/** The statuses to move to: the four, less the one it has. */
function MoveMenu({ w, moves, onDone }: { w: Worktree; moves: Moves; onDone: () => void }) {
  return (
    <View testID={`wt-menu-${w.path}`} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
      {STATUSES.filter((s) => s !== w.status).map((s) => (
        <Button
          key={s}
          testID={`wt-to-${s}`}
          label={s}
          onPress={() => (moves.move(w.path, s), onDone())}
          style={{ paddingVertical: space.sm }}
        />
      ))}
    </View>
  );
}

function Card(props: { w: Worktree; moves: Moves; onOpen: () => void }) {
  const c = usePalette();
  const { w, moves } = props;
  const [menu, setMenu] = useState(false);
  return (
    <View testID={`wt-${w.path}`} style={{ padding: space.lg, gap: space.sm, borderTopWidth: 1, borderTopColor: c.line }}>
      <Pressable testID={`wt-open-${w.path}`} accessibilityRole="button" onPress={props.onOpen} style={{ gap: 2 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: space.sm }}>
          <Text style={{ color: c.ink, fontWeight: '600', flexShrink: 1 }}>{w.branch}</Text>
          <Chip word={w.status} />
        </View>
        <Text style={{ color: c.muted, fontSize: 12 }}>
          {w.repo} · {w.owner ?? 'no owner'} · {files(w)}
        </Text>
      </Pressable>
      <Notes w={w} moves={moves} />
      {moves.enabled && !moves.moving[w.path] ? (
        menu ? (
          <MoveMenu w={w} moves={moves} onDone={() => setMenu(false)} />
        ) : (
          <Button
            testID={`wt-move-${w.path}`}
            label="Move to…"
            onPress={() => setMenu(true)}
            style={{ alignSelf: 'flex-start', paddingVertical: space.sm }}
          />
        )
      ) : null}
    </View>
  );
}

function Fact(props: { label: string; children: React.ReactNode }) {
  const c = usePalette();
  return (
    <View style={{ gap: 2 }}>
      <Text style={{ color: c.muted, fontSize: 12 }}>{props.label}</Text>
      {props.children}
    </View>
  );
}

function One(props: {
  w: Worktree | undefined;
  path: string;
  all: Worktree[];
  moves: Moves;
  onOpen: (p: string) => void;
  onAgent: (n: string) => void;
}) {
  const c = usePalette();
  const { w, moves } = props;
  const [menu, setMenu] = useState(false);
  if (!w) return <Text style={{ color: c.muted, padding: space.lg }}>No worktree at {props.path}.</Text>;
  const text = (s: string, mono = false) => <Text style={{ color: c.ink, fontFamily: mono ? 'monospace' : undefined }}>{s}</Text>;
  const link = (p: string) => (
    <Pressable key={p} accessibilityRole="button" onPress={() => props.onOpen(p)}>
      <Text style={{ color: c.ink, textDecorationLine: 'underline' }}>{props.all.find((x) => x.path === p)?.branch ?? p}</Text>
    </Pressable>
  );
  return (
    <ScrollView testID="wt-detail" contentContainerStyle={{ padding: space.lg, gap: space.md }}>
      <Text style={{ color: c.ink, fontSize: 18, fontWeight: '700' }}>{w.branch}</Text>
      <Fact label="Repo">{text(w.repo)}</Fact>
      <Fact label="Path">{text(w.path, true)}</Fact>
      <Fact label="Owner">{text(w.owner ?? 'no owner')}</Fact>
      <Fact label="Status">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <Chip word={w.status} />
          {moves.enabled && !moves.moving[w.path] && !menu ? (
            <Button testID="wt-move" label="Move to…" onPress={() => setMenu(true)} style={{ paddingVertical: space.sm }} />
          ) : null}
        </View>
        {menu ? <MoveMenu w={w} moves={moves} onDone={() => setMenu(false)} /> : null}
        <Notes w={w} moves={moves} />
      </Fact>
      {w.parent ? <Fact label="Parent">{link(w.parent)}</Fact> : null}
      {w.children.length ? <Fact label="Children">{w.children.map(link)}</Fact> : null}
      {w.owner ? <Button testID="wt-agent" kind="yes" label="Open agent" onPress={() => props.onAgent(w.owner!)} /> : null}
      <Fact label={w.changed.length ? `Changed files (${files(w)})` : 'Changed files'}>
        {w.changed.length === 0 ? <Text style={{ color: c.muted }}>No changed files.</Text> : null}
        {w.changed.map((f) => (
          <Text key={f.path} style={{ color: c.ink, fontFamily: 'monospace', fontSize: 12 }}>
            {f.status} {f.path} +{f.added} −{f.deleted}
          </Text>
        ))}
      </Fact>
    </ScrollView>
  );
}

export function Worktrees(props: { onClose: () => void; onAgent: (name: string) => void }) {
  const c = usePalette();
  const query = useWorktrees();
  const moves = useMoves(query.refetch);
  const { served } = useServes('worktrees.list');
  const [open, setOpen] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const all = query.data?.worktrees ?? [];
  const find = search.trim().toLowerCase();
  const shown = all
    .filter((w) => !find || w.branch.toLowerCase().includes(find) || w.path.toLowerCase().includes(find))
    .sort((a, b) => a.repo.localeCompare(b.repo) || a.branch.localeCompare(b.branch));
  return (
    <View testID="worktrees" style={{ flex: 1 }}>
      <Button
        testID="wt-back"
        label={open ? '‹ Worktrees' : '‹ Back'}
        onPress={() => (open ? setOpen(null) : props.onClose())}
        style={{ margin: space.lg, alignSelf: 'flex-start' }}
      />
      {open ? null : <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700', paddingHorizontal: space.lg }}>Worktrees</Text>}
      <Loadable
        query={query}
        served={served}
        isEmpty={all.length === 0}
        skeleton="worktrees"
        label="worktrees"
        empty="No worktrees yet. One appears when an agent is started in a repo."
      >
        {open ? (
          <One w={all.find((w) => w.path === open)} path={open} all={all} moves={moves} onOpen={setOpen} onAgent={props.onAgent} />
        ) : (
          <>
            <TextInput
              testID="wt-search"
              accessibilityLabel="Search worktrees"
              placeholder="Search branch or path"
              placeholderTextColor={c.muted}
              value={search}
              onChangeText={setSearch}
              autoCapitalize="none"
              style={{
                color: c.ink,
                borderWidth: 1,
                borderColor: c.line,
                borderRadius: radius.md,
                padding: space.md,
                margin: space.lg,
                minHeight: 44,
              }}
            />
            {shown.length === 0 ? (
              <Text testID="wt-none" style={{ color: c.muted, paddingHorizontal: space.lg }}>
                No worktree matches.
              </Text>
            ) : (
              <FlatList
                data={shown}
                keyExtractor={(w) => w.path}
                renderItem={({ item: w }) => <Card w={w} moves={moves} onOpen={() => setOpen(w.path)} />}
              />
            )}
          </>
        )}
      </Loadable>
    </View>
  );
}
