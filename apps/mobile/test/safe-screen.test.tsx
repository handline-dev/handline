import { describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import { act } from 'react';
import { SafeScreen } from '#src/screens/parts.tsx';

vi.mock('react-native-safe-area-context', () => ({
  SafeAreaProvider: ({ children }: { children: React.ReactNode }) => children,
  useSafeAreaInsets: () => ({ top: 31, bottom: 24, left: 0, right: 0 }),
}));

describe('SafeScreen', () => {
  it('pads the window insets: the status bar above and the navigation bar below', async () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    await act(async () =>
      createRoot(host).render(
        <SafeScreen background="#000" testID="s">
          x
        </SafeScreen>,
      ),
    );
    const style = getComputedStyle(host.querySelector('[data-testid="s"]')!);
    expect([style.paddingTop, style.paddingBottom]).toEqual(['31px', '24px']);
  });
});
