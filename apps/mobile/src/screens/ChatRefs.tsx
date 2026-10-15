// The list a `#` at the end of the compose box opens: the agent's newest requests (chat.refs); a pick is a reference as a picked message is.
// A daemon that does not list chat.refs shows nothing.
import { Pressable, Text, View } from 'react-native';
import { useChatRefs, useServes } from '@handline/app-data';
import type { ChatMessage } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';

/** What a reply needs of the message it answers. */
export type Ref = Pick<ChatMessage, 'id' | 'from' | 'to' | 'body'>;

const TRAILING = /(^|\s)#(\d*)$/;

export function ChatRefs(props: { agent: string; text: string; onPick: (r: Ref, rest: string) => void }) {
  const c = usePalette();
  const { served } = useServes('chat.refs');
  const at = TRAILING.exec(props.text);
  const refs = useChatRefs(props.agent, served && !!at);
  if (!served || !at) return null;
  const rows = (refs.data?.requests ?? []).filter((r) => String(r.request).startsWith(at[2]!));
  if (!rows.length) return null;
  const rest = props.text.slice(0, at.index + at[1]!.length);
  return (
    <View
      testID="chat-refs"
      style={{ marginHorizontal: space.sm, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, backgroundColor: c.panel }}
    >
      {rows.slice(0, 6).map((r) => (
        <Pressable
          key={r.request}
          testID={`ref-${r.request}`}
          onPress={() => props.onPick({ id: r.request, from: r.from, to: r.to, body: r.line }, rest)}
          style={{ padding: space.sm }}
        >
          <Text numberOfLines={1} style={{ color: c.ink }}>
            #{r.request} {r.line}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}
