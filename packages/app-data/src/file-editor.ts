// The edit state of one file in an agent's worktree, shared by the PC File tab and the phone's: the read, the draft, the etag the
// read gave and the write against it.
import { useEffect, useState } from 'react';
import { classify, sentence } from './errors.ts';
import { useServes } from './provider.ts';
import { useFile } from './read.ts';
import { useWrite } from './write.ts';

export const EDITABLE = /\.(md|json)$/i;

/** JSON is shown indented; text that does not parse is shown as it is. */
export function shown(path: string, text: string): string {
  if (!/\.json$/i.test(path)) return text;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function useFileEditor(agent: string, path: string) {
  const q = useFile(agent, path);
  const { served } = useServes('file.read');
  const save = useWrite('file.write', `${agent}:${path}`);
  const [draft, setDraft] = useState<string | null>(null);
  const [etag, setEtag] = useState('');
  const [note, setNote] = useState('');
  const d = q.data;
  useEffect(() => {
    setDraft(null);
    setNote('');
  }, [path]);
  useEffect(() => {
    if (d) setEtag(d.etag);
  }, [d]);
  const submit = async () => {
    if (draft === null) return;
    try {
      const r = await save.mutateAsync({ agent, path, text: draft, etag });
      setEtag(r.etag);
      setDraft(null);
      setNote('');
      void q.refetch();
    } catch (e) {
      const f = classify(e);
      setNote(f.kind === 'refused' && f.gate === 'stale' ? 'The file changed since you opened it. Reload it, then make the edit again.' : sentence(f));
    }
  };
  const edit = () => d && setDraft(shown(path, d.text));
  const discard = () => (setDraft(null), setNote(''));
  return { q, served, saving: save.isPending, draft, setDraft, note, submit, edit, discard, editable: !!d && !d.truncated && EDITABLE.test(path) };
}
