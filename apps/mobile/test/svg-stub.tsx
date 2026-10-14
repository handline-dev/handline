// react-native-svg under react-native-web in a DOM: shapes become plain elements that keep testID and onPress, so a screen that draws
// with it can be found and pressed. What the real library draws is not tested here.
import type { ReactNode } from 'react';

type P = { children?: ReactNode; testID?: string; onPress?: () => void; d?: string };
const el = (tag: 'div' | 'span') => (p: P) => {
  const T = tag;
  return (
    <T data-testid={p.testID} data-d={p.d} role={p.onPress ? 'button' : undefined} onClick={p.onPress}>
      {p.children}
    </T>
  );
};
export default el('div');
export const G = el('div');
export const Path = el('span');
export const Rect = el('span');
export const Text = el('span');
