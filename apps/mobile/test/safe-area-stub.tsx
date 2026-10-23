// The native insets are not there in a DOM: a screen with no bars around it.
export function SafeAreaProvider({ children }: { children?: React.ReactNode }) {
  return <>{children}</>;
}

export function useSafeAreaInsets() {
  return { top: 0, bottom: 0, left: 0, right: 0 };
}
