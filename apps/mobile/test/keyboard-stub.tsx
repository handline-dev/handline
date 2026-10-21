// react-native-keyboard-controller is native; in the DOM there is no keyboard, so these draw as their plain counterparts.
import type { ReactNode } from 'react';

export const KeyboardProvider = (props: { children: ReactNode }) => props.children;
export const useKeyboardState = <T,>(select: (s: { isVisible: boolean }) => T): T => select({ isVisible: false });
