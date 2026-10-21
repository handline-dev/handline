// react-native-keyboard-controller is native; in the DOM there is no keyboard, so these draw as their plain counterparts.
import { forwardRef, type ReactNode } from 'react';
import { ScrollView, View, type ScrollViewProps, type ViewProps } from 'react-native';

export const KeyboardProvider = (props: { children: ReactNode }) => props.children;
export const KeyboardAvoidingView = (props: ViewProps & { behavior?: string }) => <View {...props} />;
export const KeyboardChatScrollView = forwardRef<ScrollView, ScrollViewProps & { blankSpace?: unknown; keyboardLiftBehavior?: string }>(
  ({ blankSpace: _b, keyboardLiftBehavior: _k, ...rest }, ref) => <ScrollView ref={ref} {...rest} />,
);
export const useKeyboardState = <T,>(select: (s: { isVisible: boolean }) => T): T => select({ isVisible: false });
