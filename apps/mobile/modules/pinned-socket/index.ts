import { requireNativeModule } from 'expo';

/** What the native module offers; `src/pinned-transport.ts` turns it into a client Transport. */
export interface PinnedSocketNative {
  connect(id: string, url: string, fingerprint: string): Promise<void>;
  send(id: string, text: string): void;
  close(id: string): void;
  addListener(event: 'message', fn: (e: { id: string; data: string }) => void): { remove(): void };
  addListener(event: 'close', fn: (e: { id: string }) => void): { remove(): void };
}

export default requireNativeModule<PinnedSocketNative>('PinnedSocket');
