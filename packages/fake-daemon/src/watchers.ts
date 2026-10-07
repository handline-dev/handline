// What a terminal watcher is sent for one of the script's notices, by the format of its attach: text gets the notice as is, styled the frames
// with colour on the rows and text's resizes and markers, bytes only markers and holders, each of the last two in its own attach's counter.
import type { Notifications } from '@handline/client';
import { styledFrame, type Attach } from './session.ts';

export function watcherNotice(a: Attach | undefined, method: string, params: unknown, cols: number): unknown {
  const marks = method === 'marker' || method === 'holder';
  if (a?.format === 'text') return params;
  if (a?.format === 'styled') {
    const p = method === 'frame' ? styledFrame(params as Notifications['frame'], cols) : params;
    return { ...(p as object), ...(marks ? { format: 'styled' } : {}), seq: ++a.seq };
  }
  if (a && marks) return { ...(params as object), format: 'bytes', seq: ++a.seq };
  return null;
}
