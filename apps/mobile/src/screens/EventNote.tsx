import { useState } from 'react';
import { View } from 'react-native';
import { noteIsLong, NOTE_LINES } from '@handline/app-core';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';
import { PrBody } from '#src/screens/PrBody.tsx';

/** A hand-off note, open under its line; one longer than a few lines is cut to its first lines with a Show more. */
export function EventNote({ id, agent, note }: { id: string; agent: string; note: string }) {
  const c = usePalette();
  const [more, setMore] = useState(false);
  const long = noteIsLong(note);
  const shown = long && !more ? note.split('\n').slice(0, NOTE_LINES).join('\n') : note;
  return (
    <View testID={`event-note-${id}`} style={{ gap: space.xs, borderWidth: 1, borderColor: c.line, borderRadius: 8, padding: space.sm }}>
      <PrBody id={`note-${id}`} agent={agent} body={shown} />
      {long ? <Button testID={`event-note-more-${id}`} label={more ? 'Show less' : 'Show more'} onPress={() => setMore(!more)} /> : null}
    </View>
  );
}
