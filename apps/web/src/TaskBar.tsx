// The bar under the Tasks list: what acts on the marked tasks, as icons with the key beside them; the words are for the tooltip and a screen reader.
import { Button, Kbd, Toolbar, VisuallyHidden } from './ui/index.ts';

type Can = { ok: boolean; why: string | undefined };

export function ActionBar(props: {
  marked: number;
  can: { discard: Can; edit: Can; merge: Can };
  onAct: (what: 'discard' | 'owner' | 'priority' | 'merge') => void;
}) {
  const { discard, edit, merge } = props.can;
  const one = (what: 'discard' | 'owner' | 'priority' | 'merge', word: string, glyph: string, key: string, can: Can, tip = word) => (
    <Button
      label={word}
      className="max-sm:min-h-[44px]"
      onClick={() => props.onAct(what)}
      disabled={!can.ok}
      title={can.why ?? tip}
    >
      <VisuallyHidden>{word}</VisuallyHidden>
      <span aria-hidden>{glyph}</span> <Kbd className="max-sm:hidden">{key}</Kbd>
    </Button>
  );
  return (
    <Toolbar label="Actions on the marked tasks">
      <span className="muted mr-auto">{props.marked ? `${props.marked} marked` : ''}</span>
      {one('discard', 'Discard', '⊘', 'd', discard)}
      {one('owner', 'Owner', '@', 'o', edit)}
      {one('priority', 'Priority', '▰', 'p', edit)}
      {one('merge', 'Merge', '⇢', 'm', merge, 'Merge: mark tasks, put the cursor on the one to keep')}
    </Toolbar>
  );
}
