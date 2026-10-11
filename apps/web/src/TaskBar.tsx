// The bar under the Tasks list: what acts on the marked tasks, as icons with the key beside them; the words are for the tooltip and a screen reader.
import { VH } from './taskui.tsx';
import { KBD } from './kbdStyle.ts';

type Can = { ok: boolean; why: string | undefined };

export function ActionBar(props: {
  marked: number;
  can: { discard: Can; edit: Can; merge: Can };
  onAct: (what: 'discard' | 'owner' | 'priority' | 'merge') => void;
}) {
  const { discard, edit, merge } = props.can;
  const one = (what: 'discard' | 'owner' | 'priority' | 'merge', word: string, glyph: string, key: string, can: Can, tip = word) => (
    <button className="btn" onClick={() => props.onAct(what)} disabled={!can.ok} title={can.why ?? tip}>
      <span className={`vh ${VH}`}>{word}</span>
      <span aria-hidden>{glyph}</span> <kbd className={KBD}>{key}</kbd>
    </button>
  );
  return (
    <div className="tbar flex flex-wrap items-center gap-2 pt-2" role="toolbar" aria-label="Actions on the marked tasks">
      <span className="muted mr-auto">{props.marked ? `${props.marked} marked` : ''}</span>
      {one('discard', 'Discard', '⊘', 'd', discard)}
      {one('owner', 'Owner', '@', 'o', edit)}
      {one('priority', 'Priority', '▰', 'p', edit)}
      {one('merge', 'Merge', '⇢', 'm', merge, 'Merge: mark tasks, put the cursor on the one to keep')}
    </div>
  );
}
