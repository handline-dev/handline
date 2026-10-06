import { useColorScheme } from 'react-native';
import { colors, type Palette } from '@handline/tokens';

export const usePalette = (): Palette => colors[useColorScheme() === 'dark' ? 'dark' : 'light'];
export const dotColor = (c: Palette, state: string): string =>
  state === 'running' ? c.run : state === 'waiting' ? c.wait : state === 'idle' ? c.run : c.idle;
