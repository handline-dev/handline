// The images of the message being written: picked from the photo library, uploaded at once, named in the send once all have arrived.
import { useState } from 'react';
import { MAX_PER_MESSAGE, useImageDrafts } from '@handline/app-data';
import { pickImages } from '../ImagePick.ts';
import { AttachButton, ImageChips } from './ImageDrafts.tsx';

export function useChatImages(canSend: boolean) {
  const d = useImageDrafts();
  const [error, setError] = useState<string | null>(null);
  const pick = () => {
    setError(null);
    pickImages(MAX_PER_MESSAGE - d.drafts.length).then(d.add, (e) =>
      setError(e instanceof Error ? e.message : 'The photo library did not open'),
    );
  };
  return {
    ids: d.ids,
    /** One is still uploading or failed: the message waits. */
    busy: d.busy,
    clear: d.clear,
    chips: <ImageChips drafts={d.drafts} problem={error ?? d.problem} onRetry={d.retry} onRemove={d.remove} />,
    button: <AttachButton onPress={pick} disabled={!canSend} />,
  };
}
